from __future__ import annotations

import json
import os
import signal
import socket
import subprocess
import sys
import threading
import time
from typing import Any

import redis

import cookies
import jobs
from function import run_yt_dlp

REDIS_URL = os.environ.get("REDIS_URL", "redis://localhost:6379")
REDIS_TTL = int(os.environ.get("REDIS_TTL", str(7 * 24 * 60 * 60)))
RETRY_COUNT = int(os.environ.get("RETRY_COUNT", "5"))
LEASE_TTL = int(os.environ.get("LEASE_TTL", "60"))
HEARTBEAT_INTERVAL = int(os.environ.get("HEARTBEAT_INTERVAL", "10"))
STOP_GRACE = int(os.environ.get("STOP_GRACE", "20"))

redis_message = "Redis client not initialized"

redis_client: redis.Redis | None = None

try:
    redis_client = redis.Redis.from_url(REDIS_URL, decode_responses=True)
    redis_client.ping()
    print("INFO: Connected to Redis:", REDIS_URL)
except Exception as e:
    print("ERROR: Failed to connect to Redis:", e)
    sys.exit(1)


def record_failure(
        key: str, auth_profile: str | None, output: str,
        url: str | None = None) -> str:
    """失敗を記録する。ログイン要求なら error_code を付け、cookie を失効扱いにする。"""
    try:
        redis_client.hincrby(key, "failed_count", 1)
    except Exception:
        print("ERROR: Failed to increment failed_count for", key)

    extra: dict[str, Any] = {
        "error": output, "failed_at": str(time.time()), "error_code": "",
        "login_url": ""}
    if cookies.classify_login_required(output):
        extra["error_code"] = "login_required"
        # プロファイルの無いジョブは、URL に対応するプロファイルへ案内する
        profile = auth_profile or cookies.resolve_profile(url)
        extra["login_url"] = cookies.login_url(profile, url) or ""
        if auth_profile:
            cookies.mark_expired(redis_client, auth_profile)
        print("WARNING: login required. profile:", auth_profile or "(none)")
    return jobs.update_status(redis_client, key, "failed", extra, REDIS_TTL)


def _load_job_from_hash(key: str) -> dict[str, Any]:
    data = redis_client.hgetall(key) or {}
    return {
        "url": data.get("url", ""),
        "options": json.loads(data.get("options", "[]") or "[]"),
        "savedir": data.get("savedir", ""),
        "filename": data.get("filename", ""),
        "auth_profile": data.get("auth_profile", ""),
        "id": key.split(":")[-1],
    }


def _execute(
        job: dict[str, Any],
        stop_requested: threading.Event) -> tuple[str, str]:
    """yt-dlp を実行する。停止指示が来たら子プロセスを止めて中断扱いにする。"""
    proc_holder: dict[str, subprocess.Popen] = {}
    result_box: dict[str, tuple[bool, str]] = {}

    def on_start(proc: subprocess.Popen) -> None:
        proc_holder["proc"] = proc

    def runner() -> None:
        result_box["result"] = run_yt_dlp(job, on_process_start=on_start)

    t = threading.Thread(target=runner)
    t.start()
    while t.is_alive():
        if stop_requested.wait(timeout=0.3):
            break

    if stop_requested.is_set():
        proc = proc_holder.get("proc")
        if proc is not None and proc.poll() is None:
            print("INFO: Stopping yt-dlp (SIGTERM) due to stop request")
            proc.send_signal(signal.SIGTERM)
            try:
                proc.wait(timeout=STOP_GRACE)
            except subprocess.TimeoutExpired:
                print("WARNING: yt-dlp did not stop in time; killing")
                proc.kill()
        t.join()
        return "interrupted", "stopped by signal (SIGTERM/SIGINT)"

    t.join()
    ok, output = result_box["result"]
    return ("completed", output) if ok else ("failed", output)


def _heartbeat_loop(
        worker_id: str, finished: threading.Event,
        stop_requested: threading.Event) -> None:
    """リースを更新し続ける。更新できない状態が LEASE_TTL 続いたら、実行中のジョブを
    停止指示があった場合と同じ扱いにして中止する(W25: 同時実行の防止)。
    リースが切れた後は、dispatcher が別 worker にジョブを渡しうるため。
    """
    last_success = time.time()
    while not finished.is_set():
        try:
            jobs.renew_lease(redis_client, worker_id, LEASE_TTL)
            last_success = time.time()
        except Exception as e:
            print("WARNING: Failed to renew lease:", e)
            if time.time() - last_success >= LEASE_TTL and not stop_requested.is_set():
                print(
                    "WARNING: Lease could not be renewed for", LEASE_TTL,
                    "seconds — stopping current job")
                stop_requested.set()
        finished.wait(HEARTBEAT_INTERVAL)


def run_once(worker_id: str, stop_requested: threading.Event) -> int:
    key: str | None = None
    job: dict[str, Any] | None = None

    try:
        key = jobs.take_retryable_failed(redis_client, RETRY_COUNT, worker_id)
        if key:
            job = _load_job_from_hash(key)
            print("INFO: Retrying failed job:", key)
        else:
            raw = jobs.take_from_queue(redis_client, worker_id)
            if raw is None:
                print("INFO: No job found in queue or retryable failed jobs; exiting")
                return 0
            try:
                job = json.loads(raw)
            except Exception:
                print("ERROR: Failed to parse job JSON:", raw)
                jobs.discard_processing(redis_client, worker_id, raw)
                return 0
            jid = job.get("id")
            if not isinstance(jid, str) or not jid.strip():
                print("ERROR: Failed to get id")
                jobs.discard_processing(redis_client, worker_id, raw)
                return 0
            key = jobs.commit_from_processing(
                redis_client, worker_id, jid, job, REDIS_TTL)
            print("INFO: Pulled job from queue:", key)
    except redis.RedisError as e:
        print("ERROR: Redis error while acquiring job:", e)
        return 1

    if stop_requested.is_set():
        # 取得直後に停止指示が来ていた場合も、実行せず中断扱いに戻す
        if jobs.is_owner(redis_client, key, worker_id):
            jobs.mark_interrupted(
                redis_client, key, consume_retry=False,
                reason="stopped before execution", ttl=REDIS_TTL)
        return 0

    status, output = _execute(job, stop_requested)

    if not jobs.is_owner(redis_client, key, worker_id):
        # dispatcher に回収され、既に別の worker の所有になっている
        print("WARNING: Lost ownership of job (reclaimed); not recording result:", key)
        return 0

    if status == "interrupted":
        jobs.mark_interrupted(
            redis_client, key, consume_retry=False, reason=output, ttl=REDIS_TTL)
        print("INFO: Job interrupted by stop signal; will retry (no retry consumed)")
        return 0
    if status == "completed":
        jobs.update_status(
            redis_client, key, "completed",
            {"completed_at": str(time.time()), "output": output}, REDIS_TTL)
        print("INFO: Job completed; exiting")
        return 0

    record_failure(key, job.get("auth_profile"), output, job.get("url"))
    print("INFO: Job failed; exiting")
    return 0


def main() -> int:
    print("INFO: Worker started — processing at most one job then exit")
    worker_id = f"{socket.gethostname()}-{os.getpid()}-{int(time.time() * 1000)}"
    stop_requested = threading.Event()
    finished = threading.Event()

    def _handle_signal(signum: int, _frame: object) -> None:
        print(f"INFO: Signal {signum} received — interrupting current job")
        stop_requested.set()

    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)

    jobs.acquire_lease(redis_client, worker_id, LEASE_TTL)
    hb_thread = threading.Thread(
        target=_heartbeat_loop, args=(worker_id, finished, stop_requested),
        daemon=True)
    hb_thread.start()

    try:
        return run_once(worker_id, stop_requested)
    except Exception as e:
        print("ERROR: Unexpected error in worker:", e)
        return 1
    finally:
        finished.set()
        jobs.release_lease(redis_client, worker_id)
        hb_thread.join(timeout=2)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("INFO: KeyboardInterrupt received — shutting down worker gracefully")
        sys.exit(0)
