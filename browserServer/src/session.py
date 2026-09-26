"""ログイン操作(セッション)の状態管理。同時に 1 件、一定時間で自動終了する。"""
from __future__ import annotations

import threading
import time
from typing import Any, Protocol
from urllib.parse import urlsplit

import cookies
import netscape


class SessionError(Exception):
    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.message = message


class Runtime(Protocol):
    novnc_port: int

    def start(self, start_url: str) -> None: ...
    def get_cookies(self) -> list[dict]: ...
    def stop(self) -> None: ...


class SessionManager:
    """idle -> starting -> running -> committing -> idle。"""

    def __init__(self, runtime: Runtime, timeout: int = 900) -> None:
        self.runtime = runtime
        self.timeout = timeout
        self._lock = threading.Lock()
        self._state = "idle"
        self._profile = ""
        self._start_url = ""
        self._deadline = 0.0
        self._timer: threading.Timer | None = None

    def info(self) -> dict[str, Any]:
        with self._lock:
            running = self._state in ("running", "committing")
            return {
                "state": self._state,
                "profile": self._profile if running else None,
                "start_url": self._start_url if running else None,
                "remaining": (
                    max(0, int(self._deadline - time.time())) if running else 0),
                "novnc_port": self.runtime.novnc_port,
            }

    def start(self, profile: object, start_url: object) -> dict[str, Any]:
        try:
            name = cookies.validate_profile(profile)
        except ValueError as e:
            raise SessionError(400, str(e)) from None
        parts = urlsplit(start_url) if isinstance(start_url, str) else None
        if (not parts or parts.scheme not in ("http", "https")
                or not parts.netloc):
            raise SessionError(400, "start_url must be an http(s) URL")

        with self._lock:
            if self._state != "idle":
                raise SessionError(409, "別のログイン操作が実行中です")
            self._state = "starting"
        try:
            self.runtime.start(start_url)  # type: ignore[arg-type]
        except Exception as e:
            print("ERROR: failed to start browser:", e)
            self._teardown()
            raise SessionError(500, "ブラウザを起動できませんでした") from None

        with self._lock:
            self._profile = name
            self._start_url = str(start_url)
            self._deadline = time.time() + self.timeout
            self._state = "running"
            self._timer = threading.Timer(self.timeout, self._expire)
            self._timer.daemon = True
            self._timer.start()
        print("INFO: login session started. profile:", name)
        return self.info()

    def commit(self) -> dict[str, Any]:
        with self._lock:
            if self._state != "running":
                raise SessionError(409, "ログイン操作が実行中ではありません")
            self._state = "committing"
            profile, start_url = self._profile, self._start_url
        try:
            all_cookies = self.runtime.get_cookies()
            kept = netscape.filter_cookies(all_cookies, start_url)
            if not kept:
                # 未ログインの可能性が高い。ブラウザは残して続けられるようにする
                with self._lock:
                    self._state = "running"
                raise SessionError(
                    422,
                    "対象サイトの cookie がありません。"
                    "ログインが完了してから保存してください")
            cookies.save_profile(profile, netscape.to_netscape(kept).encode())
        except SessionError:
            raise
        except Exception as e:
            print("ERROR: failed to save cookies:", e)
            self._teardown()
            raise SessionError(500, "cookie を保存できませんでした") from None
        domain = netscape.site_domain(start_url)
        self._teardown()
        print("INFO: cookies saved. profile:", profile, "count:", len(kept))
        return {"profile": profile, "saved": len(kept), "domain": domain}

    def cancel(self) -> None:
        with self._lock:
            if self._state == "committing":
                raise SessionError(409, "保存中です")
        self._teardown()

    def _expire(self) -> None:
        with self._lock:
            if self._state != "running":
                return
        print("INFO: login session timed out")
        self._teardown()

    def _teardown(self) -> None:
        """ブラウザとプロファイルを必ず破棄して idle に戻る。"""
        with self._lock:
            timer, self._timer = self._timer, None
        if timer:
            timer.cancel()
        try:
            self.runtime.stop()
        except Exception as e:
            print("WARNING: failed to stop browser:", e)
        with self._lock:
            self._state = "idle"
            self._profile = self._start_url = ""
            self._deadline = 0.0
