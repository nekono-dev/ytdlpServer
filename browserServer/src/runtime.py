"""ログイン用ブラウザ(Xvfb + x11vnc + noVNC + Chromium)の起動・cookie 回収・終了。

CDP(9222)と RFB(5900)はコンテナ内の loopback のみ。LAN へ出すのは noVNC だけ。
"""
from __future__ import annotations

import contextlib
import json
import os
import shutil
import subprocess
import tempfile
import time
import urllib.request
from pathlib import Path

import websocket

DISPLAY = ":99"
RFB_PORT = 5900
CDP_PORT = 9222
NOVNC_WEB = "/usr/share/novnc"
START_TIMEOUT = 30


class ChromiumRuntime:
    def __init__(self, novnc_port: int = 6080) -> None:
        self.novnc_port = novnc_port
        self._procs: list[subprocess.Popen] = []
        self._profile_dir: str | None = None

    def _spawn(self, cmd: list[str]) -> None:
        env = {**os.environ, "DISPLAY": DISPLAY}
        self._procs.append(subprocess.Popen(
            cmd, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))

    @staticmethod
    def _wait(check: object, what: str) -> None:
        deadline = time.monotonic() + START_TIMEOUT
        while time.monotonic() < deadline:
            with contextlib.suppress(Exception):
                if check():  # type: ignore[operator]
                    return
            time.sleep(0.3)
        msg = f"{what} did not become ready"
        raise RuntimeError(msg)

    def start(self, start_url: str) -> None:
        self._profile_dir = tempfile.mkdtemp(prefix="profile-")
        try:
            # 強制終了で残った Xvfb のロックを除く
            for stale in ("/tmp/.X99-lock", "/tmp/.X11-unix/X99"):  # noqa: S108
                Path(stale).unlink(missing_ok=True)
            self._spawn([
                "Xvfb", DISPLAY, "-screen", "0", "1280x800x24", "-nolisten", "tcp"])
            self._wait(lambda: Path("/tmp/.X11-unix/X99").exists(), "Xvfb")  # noqa: S108
            self._spawn(["x11vnc", "-display", DISPLAY, "-forever", "-shared", "-nopw",
                         "-localhost", "-rfbport", str(RFB_PORT), "-quiet"])
            self._spawn(["websockify", "--web", NOVNC_WEB,
                         str(self.novnc_port), f"localhost:{RFB_PORT}"])
            self._spawn([
                "chromium", "--no-sandbox", "--disable-dev-shm-usage", "--disable-gpu",
                f"--user-data-dir={self._profile_dir}",
                f"--remote-debugging-port={CDP_PORT}",
                "--window-position=0,0", "--window-size=1280,800",
                "--no-first-run", "--no-default-browser-check", "--lang=ja",
                start_url])
            self._wait(self._cdp_ready, "Chromium")
        except Exception:
            self.stop()
            raise

    @staticmethod
    def _cdp_ready() -> bool:
        with urllib.request.urlopen(
                f"http://127.0.0.1:{CDP_PORT}/json/version", timeout=2) as r:
            return r.status == 200

    def get_cookies(self) -> list[dict]:
        with urllib.request.urlopen(
                f"http://127.0.0.1:{CDP_PORT}/json/version", timeout=5) as r:
            url = json.load(r)["webSocketDebuggerUrl"]
        # Origin ヘッダがあると Chromium が 403 で拒否するため付けない
        ws = websocket.create_connection(url, suppress_origin=True, timeout=10)
        try:
            ws.send(json.dumps({"id": 1, "method": "Storage.getCookies"}))
            while True:
                reply = json.loads(ws.recv())
                if reply.get("id") == 1:
                    if "error" in reply:
                        msg = f"CDP error: {reply['error'].get('message')}"
                        raise RuntimeError(msg)
                    return reply["result"]["cookies"]
        finally:
            ws.close()

    @staticmethod
    def _reap(proc: subprocess.Popen) -> None:
        try:
            proc.wait(timeout=5)
        except Exception:
            with contextlib.suppress(Exception):
                proc.kill()
                proc.wait(timeout=2)

    def stop(self) -> None:
        for proc in reversed(self._procs):
            with contextlib.suppress(Exception):
                proc.terminate()
        for proc in self._procs:
            self._reap(proc)
        self._procs = []
        if self._profile_dir:
            shutil.rmtree(self._profile_dir, ignore_errors=True)
            self._profile_dir = None
