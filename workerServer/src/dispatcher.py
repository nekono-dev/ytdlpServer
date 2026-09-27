"""dispatcher: ジョブがあるときだけ worker を起動する常駐プロセス。

設計は specs/workerServer/design.md の「イベント駆動の worker 起動」
「yt-dlp の更新」を参照。dispatcher 自身は yt-dlp・ffmpeg を使わず、
Redis からはジョブの件数と状態 (URL・オプションは読まない) だけを見る。
"""
from __future__ import annotations

import os
import signal
import socket
import sys
import threading
import time
from pathlib import Path

import redis

import jobs
import updater
from backends import DockerBackend, ProcessBackend

EVENTS_CHANNEL = "ytdlp:events"
BACKOFF_BASE = 5
BACKOFF_MAX = 60


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, str(default)))
    except (TypeError, ValueError):
        return default


class Config:
    def __init__(self) -> None:
        self.redis_url = os.environ.get("REDIS_URL", "redis://localhost:6379")
        self.retry_count = _env_int("RETRY_COUNT", 5)
        self.redis_ttl = _env_int("REDIS_TTL", 7 * 24 * 60 * 60)
        # Alpine の既存設定 (WORKER_COUNT) を WORKER_MAX の別名として引き継ぐ
        self.worker_max = _env_int(
            "WORKER_MAX", _env_int("WORKER_COUNT", 1))
        self.scan_interval = _env_int("DISPATCH_SCAN_INTERVAL", 30)
        self.lease_ttl = _env_int("LEASE_TTL", 60)
        self.inprogress_stale = _env_int("INPROGRESS_STALE", 21600)
        self.mode = os.environ.get("DISPATCH_MODE", "process")
        self.docker_host = os.environ.get(
            "DOCKER_HOST", "tcp://docker-proxy:2375")
        self.self_id = os.environ.get("DISPATCH_SELF_ID") or socket.gethostname()
        self.stop_timeout = _env_int("STOP_GRACE", 20) + 10


class Dispatcher:
    def __init__(self, cfg: Config, backend, redis_client: redis.Redis,
                 updater_cfg: updater.UpdaterConfig) -> None:
        self.cfg = cfg
        self.backend = backend
        self.redis = redis_client
        self.updater_cfg = updater_cfg
        self.stopping = threading.Event()
        self.wake = threading.Event()
        self._force_update = threading.Event()
        self._consecutive_failures = 0

    def run(self) -> None:
        running = self.backend.reconcile()
        print(
            f"INFO: dispatcher starting. mode={self.cfg.mode} "
            f"worker_max={self.cfg.worker_max} running={running}")
        signal.signal(signal.SIGTERM, self._handle_signal)
        signal.signal(signal.SIGINT, self._handle_signal)
        threading.Thread(target=self._updater_loop, daemon=True).start()
        threading.Thread(target=self._pubsub_loop, daemon=True).start()
        threading.Thread(target=self._backend_wake_bridge, daemon=True).start()
        try:
            self._main_loop()
        finally:
            print("INFO: dispatcher stopping running workers...")
            self.backend.stop_all(timeout=self.cfg.stop_timeout)

    def _handle_signal(self, signum: int, _frame: object) -> None:
        print(f"INFO: dispatcher received signal {signum}, shutting down")
        self.stopping.set()
        self.wake.set()

    def _backend_wake_bridge(self) -> None:
        while not self.stopping.is_set():
            if self.backend.wake_event.wait(timeout=1):
                self.backend.wake_event.clear()
                self.wake.set()

    def _updater_loop(self) -> None:
        updater.check_and_update(self.redis, self.updater_cfg)
        while not self.stopping.is_set():
            if self._force_update.wait(timeout=60):
                self._force_update.clear()
                updater.check_and_update(self.redis, self.updater_cfg, force=True)
            else:
                updater.check_and_update(self.redis, self.updater_cfg)

    def _pubsub_loop(self) -> None:
        while not self.stopping.is_set():
            try:
                pubsub = self.redis.pubsub()
                pubsub.subscribe(EVENTS_CHANNEL)
                for message in pubsub.listen():
                    if self.stopping.is_set():
                        break
                    if message.get("type") != "message":
                        continue
                    if message.get("data") == "check_update":
                        self._force_update.set()
                    else:
                        self.wake.set()
            except Exception as e:  # noqa: BLE001
                print("WARNING: Redis pub/sub error (retrying):", e)
                if self.stopping.wait(timeout=5):
                    break

    def _consume_exit_codes(self) -> None:
        for rc in self.backend.pop_recent_exit_codes():
            if rc == 0:
                self._consecutive_failures = 0
            else:
                self._consecutive_failures += 1
                print(f"WARNING: worker exited with code {rc}")

    def _backoff_seconds(self) -> float:
        if self._consecutive_failures <= 0:
            return 0
        return min(
            BACKOFF_MAX, BACKOFF_BASE * (2 ** (self._consecutive_failures - 1)))

    def _main_loop(self) -> None:
        next_allowed_start = 0.0
        while not self.stopping.is_set():
            self._consume_exit_codes()
            try:
                reclaimed = jobs.reclaim_stale(
                    self.redis, inprogress_stale=self.cfg.inprogress_stale,
                    ttl=self.cfg.redis_ttl)
                if reclaimed:
                    print(f"INFO: reclaimed {reclaimed} interrupted job(s)")
            except Exception as e:  # noqa: BLE001
                print("WARNING: reclaim_stale failed:", e)

            now = time.time()
            if now >= next_allowed_start:
                try:
                    running = self.backend.running_count()
                except Exception as e:  # noqa: BLE001
                    print("WARNING: Failed to count running workers:", e)
                    running = self.cfg.worker_max
                free = max(0, self.cfg.worker_max - running)
                if free > 0:
                    try:
                        pending = jobs.count_pending_targets(
                            self.redis, self.cfg.retry_count, cap=free)
                    except Exception as e:  # noqa: BLE001
                        print("WARNING: Failed to count pending jobs:", e)
                        pending = 0
                    to_start = min(free, pending)
                    for _ in range(to_start):
                        self._start_one()
                    backoff = self._backoff_seconds()
                    if backoff:
                        next_allowed_start = time.time() + backoff

            self.wake.wait(timeout=self.cfg.scan_interval)
            self.wake.clear()

    def _start_one(self) -> None:
        try:
            self.backend.start_worker()
        except Exception as e:  # noqa: BLE001
            print("ERROR: Failed to start worker:", e)
            self._consecutive_failures += 1


def _build_backend(cfg: Config):
    if cfg.mode == "docker":
        return DockerBackend(
            docker_host=cfg.docker_host, self_id=cfg.self_id,
            worker_command=["python3", "-u", "/workspace/main.py"],
            stop_timeout=cfg.stop_timeout)
    worker_script = Path(__file__).with_name("main.py")
    return ProcessBackend(
        cmd=[sys.executable, "-u", str(worker_script)], env=dict(os.environ))


def main() -> int:
    cfg = Config()
    try:
        redis_client = redis.Redis.from_url(cfg.redis_url, decode_responses=True)
        redis_client.ping()
        print("INFO: Connected to Redis:", cfg.redis_url)
    except Exception as e:  # noqa: BLE001
        print("ERROR: Failed to connect to Redis:", e)
        return 1

    backend = _build_backend(cfg)
    updater_cfg = updater.UpdaterConfig.from_env()
    dispatcher = Dispatcher(cfg, backend, redis_client, updater_cfg)
    dispatcher.run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
