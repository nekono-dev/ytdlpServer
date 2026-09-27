"""worker の起動先を抽象化する。

- ProcessBackend: Alpine 向け。dispatcher の子プロセスとして worker を起動する。
- DockerBackend: Docker Compose 向け。docker-socket-proxy 経由で、dispatcher と
  同じイメージ・マウント・ネットワークのコンテナを作成・起動する。

設計は specs/workerServer/design.md の「worker の起動 (Docker Compose / Alpine)」を参照。
"""
from __future__ import annotations

import subprocess
import threading
from collections import deque
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Sequence

WORKER_LABEL_KEY = "ytdlp.role"
WORKER_LABEL_VALUE = "worker"


class ProcessBackend:
    """dispatcher の子プロセスとして worker を起動する (Alpine)。"""

    def __init__(self, cmd: Sequence[str], env: dict[str, str]) -> None:
        self._cmd = list(cmd)
        self._env = dict(env)
        self._lock = threading.Lock()
        self._procs: dict[int, subprocess.Popen] = {}
        self._recent_exit_codes: deque[int] = deque(maxlen=100)
        self.wake_event = threading.Event()

    def reconcile(self) -> int:
        # 子プロセスは dispatcher の再起動で必ず一緒に終了するため、引き継ぎは無い。
        return 0

    def running_count(self) -> int:
        with self._lock:
            return len(self._procs)

    def start_worker(self) -> None:
        proc = subprocess.Popen(self._cmd, env=self._env)  # noqa: S603
        with self._lock:
            self._procs[proc.pid] = proc
        threading.Thread(target=self._wait_for, args=(proc,), daemon=True).start()

    def _wait_for(self, proc: subprocess.Popen) -> None:
        rc = proc.wait()
        with self._lock:
            self._procs.pop(proc.pid, None)
            self._recent_exit_codes.append(rc)
        self.wake_event.set()

    def pop_recent_exit_codes(self) -> list[int]:
        with self._lock:
            codes = list(self._recent_exit_codes)
            self._recent_exit_codes.clear()
        return codes

    def stop_all(self, timeout: float) -> None:
        with self._lock:
            procs = list(self._procs.values())
        for p in procs:
            p.terminate()
        for p in procs:
            try:
                p.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                p.kill()


class DockerBackend:
    """docker-socket-proxy 経由で worker コンテナを起動する (Docker Compose)。

    dispatcher 自身のコンテナ (`self_id`、既定でホスト名 = コンテナ ID) を inspect し、
    同じイメージ・マウント (`volumes_from`)・ネットワーク (`network_mode=container:`) で
    worker コンテナを作る。ジョブの内容 (URL・オプション) は読まない。
    """

    def __init__(self, docker_host: str, self_id: str, worker_command: list[str],
                 stop_timeout: int = 30) -> None:
        import docker  # noqa: PLC0415 (Compose 以外では未導入のため遅延 import)

        self._docker = docker
        self.client = docker.DockerClient(base_url=docker_host)
        self.self_id = self_id
        self.worker_command = worker_command
        self.stop_timeout = stop_timeout
        self.wake_event = threading.Event()
        self._lock = threading.Lock()
        self._recent_exit_codes: deque[int] = deque(maxlen=100)
        self._events_ok = True
        threading.Thread(target=self._watch_events, daemon=True).start()

    def _filters(self) -> dict:
        return {"label": [f"{WORKER_LABEL_KEY}={WORKER_LABEL_VALUE}"]}

    def _list_workers(self) -> list:
        return self.client.containers.list(filters=self._filters())

    def reconcile(self) -> int:
        return len(self._list_workers())

    def running_count(self) -> int:
        try:
            return len(self._list_workers())
        except Exception as e:  # noqa: BLE001
            print("WARNING: Failed to list worker containers:", e)
            return 0

    def start_worker(self) -> None:
        self_container = self.client.containers.get(self.self_id)
        # 画像 ID をそのまま使う (`.image` プロパティは IMAGES API を追加で要求する
        # ため使わない。proxy には CONTAINERS/POST/EVENTS しか許可していない)。
        image = self_container.attrs["Image"]
        env = self_container.attrs.get("Config", {}).get("Env", [])
        try:
            # stop_timeout は containers.run() の対応キーワードに無いため渡さない。
            # 停止時は stop_all() が毎回 timeout を明示して stop() を呼ぶ (自前で管理)。
            self.client.containers.run(
                image=image,
                command=self.worker_command,
                environment=env,
                volumes_from=[self.self_id],
                network_mode=f"container:{self.self_id}",
                labels={WORKER_LABEL_KEY: WORKER_LABEL_VALUE},
                detach=True,
                auto_remove=True,
            )
        except Exception as e:  # noqa: BLE001
            print("ERROR: Failed to start worker container:", e)
            with self._lock:
                self._recent_exit_codes.append(1)
            self.wake_event.set()

    def pop_recent_exit_codes(self) -> list[int]:
        with self._lock:
            codes = list(self._recent_exit_codes)
            self._recent_exit_codes.clear()
        return codes

    def stop_all(self, timeout: float) -> None:
        try:
            workers = self._list_workers()
        except Exception as e:  # noqa: BLE001
            print("WARNING: Failed to list worker containers for stop:", e)
            return
        for c in workers:
            try:
                c.stop(timeout=int(timeout))
            except Exception as e:  # noqa: BLE001
                print("WARNING: Failed to stop worker container", c.id, ":", e)

    def _watch_events(self) -> None:
        """worker コンテナの終了 (die) を検知して即時に起床する (best-effort)。

        docker-socket-proxy が EVENTS を許可していない・接続が切れた場合は諦めて
        定期スキャンでの発見に任せる (機能上は必須ではない)。
        """
        filters = {**self._filters(), "type": ["container"], "event": ["die"]}
        try:
            for event in self.client.events(decode=True, filters=filters):
                code = event.get("Actor", {}).get("Attributes", {}).get(
                    "exitCode", "0")
                with self._lock:
                    try:
                        self._recent_exit_codes.append(int(code))
                    except ValueError:
                        self._recent_exit_codes.append(1)
                self.wake_event.set()
        except Exception as e:  # noqa: BLE001
            self._events_ok = False
            print(
                "WARNING: Docker events stream unavailable "
                "(falling back to periodic scan only):", e)
