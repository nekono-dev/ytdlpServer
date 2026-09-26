"""ログイン操作(セッション)の状態管理。同時に 1 件、一定時間で自動終了する。"""
from __future__ import annotations

import asyncio
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


class Browser(Protocol):
    clients: set[Any]

    async def start(self, start_url: str) -> None: ...
    async def get_cookies(self) -> list[dict]: ...
    async def stop(self) -> None: ...
    async def handle_input(self, m: dict, client: Any) -> None: ...  # noqa: ANN401


class BrowserFactory(Protocol):
    def __call__(self, *, mobile: bool) -> Browser: ...


class SessionManager:
    """idle -> starting -> running -> committing -> idle。"""

    def __init__(self, factory: BrowserFactory, timeout: float = 900) -> None:
        self.factory = factory
        self.timeout = timeout
        self.browser: Browser | None = None
        self._lock = asyncio.Lock()
        self._state = "idle"
        self._profile = ""
        self._start_url = ""
        self._domains: list[str] = []
        self._is_new = False      # プリセット・履歴に無い新しいサイトか
        self._mobile = False
        self._deadline = 0.0
        self._timer: asyncio.TimerHandle | None = None

    @property
    def state(self) -> str:
        return self._state

    def info(self) -> dict[str, Any]:
        running = self._state in ("running", "committing")
        return {
            "state": self._state,
            "profile": self._profile if running else None,
            "remaining": (
                max(0, int(self._deadline - time.time())) if running else 0),
            "mobile": self._mobile if running else None,
        }

    async def start(self, profile: object, start_url: object = None,
                    *, mobile: bool = False) -> dict[str, Any]:
        """ログイン用ブラウザを起動する。

        profile がプリセット・履歴にあれば、その開始 URL と対象ドメインを使う
        (start_url は無視)。無ければ新しいサイトとして start_url を使い、
        保存に成功したら履歴に追加する。
        """
        try:
            name = cookies.validate_profile(profile)
        except ValueError as e:
            raise SessionError(400, str(e)) from None
        entry = cookies.find_entry(name)
        if entry:
            start_url, domains, is_new = entry["start_url"], entry["domains"], False
        else:
            parts = urlsplit(start_url) if isinstance(start_url, str) else None
            if (not parts or parts.scheme not in ("http", "https")
                    or not parts.netloc):
                raise SessionError(400, "start_url must be an http(s) URL")
            domains, is_new = [netscape.site_domain(str(start_url))], True

        async with self._lock:
            if self._state != "idle":
                raise SessionError(409, "別のログイン操作が実行中です")
            self._state = "starting"
            browser = self.factory(mobile=bool(mobile))
            try:
                await browser.start(str(start_url))
            except Exception as e:
                print("ERROR: failed to start browser:", e)
                await self._stop_browser(browser)
                self._state = "idle"
                raise SessionError(500, "ブラウザを起動できませんでした") from None
            self.browser = browser
            self._profile, self._start_url = name, str(start_url)
            self._domains, self._is_new = list(domains), is_new
            self._mobile = bool(mobile)
            self._deadline = time.time() + self.timeout
            loop = asyncio.get_running_loop()
            self._timer = loop.call_later(
                self.timeout, lambda: loop.create_task(self._expire()))
            self._state = "running"
        print("INFO: login session started. profile:", name, "mobile:", bool(mobile))
        return self.info()

    async def commit(self) -> dict[str, Any]:
        async with self._lock:
            if self._state != "running" or not self.browser:
                raise SessionError(409, "ログイン操作が実行中ではありません")
            self._state = "committing"
            profile, start_url = self._profile, self._start_url
            domains, is_new = self._domains, self._is_new
            try:
                kept = netscape.filter_cookies(
                    await self.browser.get_cookies(), domains)
                if not kept:
                    # 未ログインの可能性が高い。ブラウザは残して続けられるようにする
                    self._state = "running"
                    raise SessionError(
                        422,
                        "対象サイトの cookie がありません。"
                        "ログインが完了してから保存してください")
                cookies.save_profile(profile, netscape.to_netscape(kept).encode())
                if is_new:
                    try:
                        cookies.add_history(profile, start_url, domains)
                    except ValueError as e:
                        # 同名が同時に追加された等。cookie の保存は成功している
                        print("WARNING: failed to add history:", e)
            except SessionError:
                raise
            except Exception as e:
                print("ERROR: failed to save cookies:", e)
                await self._teardown()
                raise SessionError(500, "cookie を保存できませんでした") from None
            await self._teardown()
        print("INFO: cookies saved. profile:", profile, "count:", len(kept))
        return {"profile": profile, "saved": len(kept), "domains": domains}

    async def cancel(self) -> None:
        if self._state == "committing":
            raise SessionError(409, "保存中です")
        async with self._lock:
            await self._teardown()

    async def _expire(self) -> None:
        async with self._lock:
            if self._state != "running":
                return
            print("INFO: login session timed out")
            await self._teardown()

    async def _teardown(self) -> None:
        """ブラウザとプロファイルを必ず破棄して idle に戻る(ロック内で呼ぶ)。"""
        if self._timer:
            self._timer.cancel()
            self._timer = None
        browser, self.browser = self.browser, None
        if browser:
            await self._stop_browser(browser)
        self._state = "idle"
        self._profile = self._start_url = ""
        self._domains, self._is_new = [], False
        self._mobile = False
        self._deadline = 0.0

    @staticmethod
    async def _stop_browser(browser: Browser) -> None:
        try:
            await browser.stop()
        except Exception as e:
            print("WARNING: failed to stop browser:", e)
