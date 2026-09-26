"""ログイン用ブラウザ(Xvfb + Chromium)の起動・画面配信・入力・cookie 回収・終了。

画面は CDP の Page.startScreencast で受け取り、WebSocket の接続先(操作画面)へ送る。
CDP(9222)はコンテナ内の loopback のみ。LAN へは出さない。
"""
from __future__ import annotations

import asyncio
import base64
import contextlib
import json
import os
import re
import shutil
import signal
import subprocess
import tempfile
from pathlib import Path
from typing import Any

import aiohttp

DISPLAY = ":99"
CDP_PORT = 9222
START_TIMEOUT = 30
X_SOCKET = Path("/tmp/.X11-unix/X99")  # noqa: S108
X_LOCK = Path("/tmp/.X99-lock")  # noqa: S108
CALL_TIMEOUT = 15

KEYS: dict[str, dict[str, Any]] = {
    "Enter": {"code": "Enter", "windowsVirtualKeyCode": 13, "text": "\r"},
    "Backspace": {"code": "Backspace", "windowsVirtualKeyCode": 8},
    "Delete": {"code": "Delete", "windowsVirtualKeyCode": 46},
    "Tab": {"code": "Tab", "windowsVirtualKeyCode": 9},
    "Escape": {"code": "Escape", "windowsVirtualKeyCode": 27},
    "ArrowLeft": {"code": "ArrowLeft", "windowsVirtualKeyCode": 37},
    "ArrowUp": {"code": "ArrowUp", "windowsVirtualKeyCode": 38},
    "ArrowRight": {"code": "ArrowRight", "windowsVirtualKeyCode": 39},
    "ArrowDown": {"code": "ArrowDown", "windowsVirtualKeyCode": 40},
    "Home": {"code": "Home", "windowsVirtualKeyCode": 36},
    "End": {"code": "End", "windowsVirtualKeyCode": 35},
}
TOUCH_TYPES = {"start": "touchStart", "move": "touchMove",
               "end": "touchEnd", "cancel": "touchCancel"}
MOUSE_TYPES = {"down": "mousePressed", "move": "mouseMoved", "up": "mouseReleased"}

# 指の下の要素が入力欄か(キーボードを出すかの判断)。iframe の中は分からないため "frame"
EDITABLE_AT = """((x, y) => {
  let e = document.elementFromPoint(x, y);
  while (e && e.shadowRoot && e.shadowRoot.elementFromPoint) {
    const inner = e.shadowRoot.elementFromPoint(x, y);
    if (!inner || inner === e) break;
    e = inner;
  }
  if (!e) return false;
  if (e.tagName === 'IFRAME') return 'frame';
  const f = e.closest('input,textarea,[contenteditable=""],[contenteditable="true"]');
  if (!f) return false;
  if (f.tagName === 'INPUT') return !['checkbox','radio','button','submit','reset',
    'image','file','range','color'].includes(f.type);
  return true;
})"""


def _remove_x_locks() -> None:
    X_LOCK.unlink(missing_ok=True)
    X_SOCKET.unlink(missing_ok=True)


def chromium_major() -> str:
    with contextlib.suppress(Exception):
        out = subprocess.run(["chromium", "--version"], capture_output=True,  # noqa: S607
                             text=True, check=False, timeout=10).stdout
        m = re.search(r"(\d+)\.", out)
        if m:
            return m.group(1)
    return "140"


def mobile_ua(version: str) -> str:
    return ("Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 "
            f"(KHTML, like Gecko) Chrome/{version}.0.0.0 Mobile Safari/537.36")


def _clamp(value: object, lo: float, hi: float, default: float) -> float:
    try:
        return max(lo, min(hi, float(value)))  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default


class ChromiumBrowser:
    """Chromium 1 つと、表示中タブへの CDP セッション。"""

    def __init__(self, *, mobile: bool) -> None:
        self.mobile = mobile
        self.procs: list[subprocess.Popen] = []
        self.profile_dir = tempfile.mkdtemp(prefix="profile-")
        self.http: aiohttp.ClientSession | None = None
        self.ws: aiohttp.ClientWebSocketResponse | None = None
        self.reader: asyncio.Task | None = None
        self.next_id = 0
        self.pending: dict[int, asyncio.Future] = {}
        self.session_id: str | None = None   # 表示中タブの CDP セッション
        self.target_id: str | None = None
        self.sessions: dict[str, str] = {}   # targetId -> sessionId
        self.viewport = {"width": 390, "height": 700, "dpr": 2.0}
        self.clients: set[Any] = set()       # 操作画面の WebSocket
        self.url = ""
        self._tasks: set[asyncio.Task] = set()
        self._casting = False   # 表示中タブの画面配信が動いているか

    # ---- 起動・終了 ------------------------------------------------------
    async def start(self, start_url: str) -> None:
        # 強制終了で残った Xvfb のロックを除く
        await asyncio.to_thread(_remove_x_locks)
        # ヘッドレスは Turnstile に弾かれるため、Xvfb を表示先にしてヘッドありで動かす
        self._spawn(["Xvfb", DISPLAY, "-screen", "0", "1280x1000x24",
                     "-nolisten", "tcp"])
        for _ in range(START_TIMEOUT * 10):
            if await asyncio.to_thread(X_SOCKET.exists):
                break
            await asyncio.sleep(0.1)
        flags = []
        if self.mobile:
            # UA は起動フラグで変える。CDP の Emulation.setUserAgentOverride は
            # Web Worker 等へ一貫して反映されず、Turnstile が失敗する
            flags.append(f"--user-agent={mobile_ua(chromium_major())}")
        self._spawn([
            "chromium", "--no-sandbox", "--disable-dev-shm-usage", "--disable-gpu",
            f"--user-data-dir={self.profile_dir}",
            f"--remote-debugging-port={CDP_PORT}",
            "--no-first-run", "--no-default-browser-check", "--lang=ja",
            "--test-type", "--window-position=0,0", "--window-size=1280,1000",
            *flags, "about:blank"])

        self.http = aiohttp.ClientSession()
        info = None
        for _ in range(START_TIMEOUT * 3):
            with contextlib.suppress(Exception):
                async with self.http.get(
                        f"http://127.0.0.1:{CDP_PORT}/json/version") as r:
                    info = await r.json()
                    break
            await asyncio.sleep(0.3)
        if not info:
            msg = "Chromium did not become ready"
            raise RuntimeError(msg)
        self.ws = await self.http.ws_connect(
            info["webSocketDebuggerUrl"], max_msg_size=0)
        self.reader = asyncio.create_task(self._read())
        await self.send("Target.setDiscoverTargets", {"discover": True})
        targets = (await self.send("Target.getTargets"))["targetInfos"]
        page = next(t for t in targets if t["type"] == "page")
        await self.switch(page["targetId"])
        await self.send("Page.navigate", {"url": start_url}, self.session_id)

    def _spawn(self, cmd: list[str]) -> None:
        # 一時ファイルもプロファイルの中へ置き、終了時にまとめて消す
        tmp = Path(self.profile_dir) / "tmp"
        tmp.mkdir(exist_ok=True)
        env = {**os.environ, "DISPLAY": DISPLAY, "TMPDIR": str(tmp)}
        # 子プロセス(レンダラ等)ごと終了できるよう、プロセスグループを分ける
        self.procs.append(subprocess.Popen(
            cmd, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            start_new_session=True))

    async def stop(self) -> None:
        for client in list(self.clients):
            with contextlib.suppress(Exception):
                await client.close()
        self.clients.clear()
        if self.reader:
            self.reader.cancel()
        with contextlib.suppress(Exception):
            if self.ws:
                await self.ws.close()
        with contextlib.suppress(Exception):
            if self.http:
                await self.http.close()
        for fut in self.pending.values():
            if not fut.done():
                fut.cancel()
        for proc in reversed(self.procs):
            with contextlib.suppress(Exception):
                os.killpg(proc.pid, signal.SIGTERM)
        for proc in self.procs:
            await asyncio.to_thread(self._reap, proc)
        self.procs = []
        # 終了間際の子プロセスが書き込むことがあるため、消えるまで数回試す
        for _ in range(5):
            await asyncio.to_thread(shutil.rmtree, self.profile_dir, ignore_errors=True)
            if not await asyncio.to_thread(Path(self.profile_dir).exists):
                break
            await asyncio.sleep(0.5)

    @staticmethod
    def _reap(proc: subprocess.Popen) -> None:
        try:
            proc.wait(timeout=5)
        except Exception:
            with contextlib.suppress(Exception):
                os.killpg(proc.pid, signal.SIGKILL)
                proc.wait(timeout=2)
        # 残った子プロセスもまとめて止める
        with contextlib.suppress(Exception):
            os.killpg(proc.pid, signal.SIGKILL)

    # ---- CDP --------------------------------------------------------------
    async def send(self, method: str, params: dict | None = None,
                   session_id: str | None = None) -> dict:
        if not self.ws:
            msg = "CDP is not connected"
            raise RuntimeError(msg)
        self.next_id += 1
        msg_id = self.next_id
        message: dict[str, Any] = {"id": msg_id, "method": method,
                                   "params": params or {}}
        if session_id:
            message["sessionId"] = session_id
        fut = asyncio.get_running_loop().create_future()
        self.pending[msg_id] = fut
        try:
            await self.ws.send_str(json.dumps(message))
            reply = await asyncio.wait_for(fut, CALL_TIMEOUT)
        finally:
            self.pending.pop(msg_id, None)
        if "error" in reply:
            msg = f"{method}: {reply['error'].get('message')}"
            raise RuntimeError(msg)
        return reply.get("result", {})

    async def _read(self) -> None:
        async for msg in self.ws:  # type: ignore[union-attr]
            if msg.type != aiohttp.WSMsgType.TEXT:
                continue
            data = json.loads(msg.data)
            if "id" in data:
                fut = self.pending.get(data["id"])
                if fut and not fut.done():
                    fut.set_result(data)
                continue
            task = asyncio.create_task(self._event(data))
            self._tasks.add(task)
            task.add_done_callback(self._tasks.discard)

    async def _event(self, ev: dict) -> None:
        method, p = ev.get("method"), ev.get("params", {})
        try:
            if method == "Page.screencastFrame":
                if ev.get("sessionId") == self.session_id:
                    await self._broadcast_bytes(base64.b64decode(p["data"]))
                await self.send("Page.screencastFrameAck",
                                {"sessionId": p["sessionId"]}, ev.get("sessionId"))
            elif method == "Target.targetCreated":
                t = p["targetInfo"]
                # 新しいタブ・ポップアップ(外部アカウントでのログイン等)を表示する
                if t["type"] == "page" and t.get("openerId"):
                    await self.switch(t["targetId"])
            elif method == "Target.targetDestroyed":
                await self._on_destroyed(p["targetId"])
            elif method == "Page.loadEventFired":
                if ev.get("sessionId") == self.session_id and not self._casting:
                    await self._start_screencast()
            elif method == "Target.targetInfoChanged":
                t = p["targetInfo"]
                if t["targetId"] == self.target_id:
                    self.url = t.get("url", "")
                    await self.broadcast({"type": "url", "url": self.url})
        except Exception as e:
            print("WARNING: CDP event handling failed:", method, e)

    async def _on_destroyed(self, target_id: str) -> None:
        self.sessions.pop(target_id, None)
        if target_id != self.target_id:
            return
        self.session_id = self.target_id = None
        targets = (await self.send("Target.getTargets"))["targetInfos"]
        pages = [t for t in targets if t["type"] == "page"]
        if pages:
            await self.switch(pages[0]["targetId"])

    # ---- タブと画面 --------------------------------------------------------
    async def _setup(self, sid: str) -> None:
        """タブの画面サイズ・タッチを、操作画面の端末に合わせる。"""
        await self._apply_metrics(sid)
        if self.mobile:
            await self.send("Emulation.setTouchEmulationEnabled",
                            {"enabled": True, "maxTouchPoints": 5}, sid)
        await self.send("Page.enable", {}, sid)

    async def _apply_metrics(self, sid: str) -> None:
        v = self.viewport
        await self.send("Emulation.setDeviceMetricsOverride", {
            "width": int(v["width"]), "height": int(v["height"]),
            "deviceScaleFactor": v["dpr"], "mobile": self.mobile}, sid)

    async def switch(self, target_id: str) -> None:
        """表示するタブを切り替え、そのタブの画面配信を始める。"""
        if self.session_id:
            with contextlib.suppress(Exception):
                await self.send("Page.stopScreencast", {}, self.session_id)
        sid = self.sessions.get(target_id)
        if not sid:
            sid = (await self.send("Target.attachToTarget",
                                   {"targetId": target_id, "flatten": True},
                                   ))["sessionId"]
            self.sessions[target_id] = sid
            await self._setup(sid)
        self.session_id, self.target_id = sid, target_id
        await self.send("Target.activateTarget", {"targetId": target_id})
        await self._start_screencast()
        info = await self.send("Target.getTargetInfo", {"targetId": target_id})
        self.url = info.get("targetInfo", {}).get("url", "")
        await self.broadcast({"type": "url", "url": self.url})

    async def _start_screencast(self) -> None:
        """画面配信を始める。ナビゲーション中(プロセスの切り替わり)は
        "Not attached to an active page" で失敗するため、少し待って再試行する。
        それでも失敗したら、ページの読み込み完了時(loadEventFired)に再開する。"""
        v = self.viewport
        params = {"format": "jpeg", "quality": 70,
                  "maxWidth": int(v["width"] * v["dpr"]),
                  "maxHeight": int(v["height"] * v["dpr"]),
                  "everyNthFrame": 1}
        self._casting = False
        for _ in range(10):
            sid = self.session_id
            if not sid:
                return
            try:
                await self.send("Page.startScreencast", params, sid)
            except RuntimeError:
                await asyncio.sleep(0.3)
                continue
            self._casting = True
            return
        print("WARNING: screencast could not start; will retry on page load")

    async def set_viewport(self, width: object, height: object, dpr: object) -> None:
        self.viewport = {"width": _clamp(width, 240, 2000, 390),
                         "height": _clamp(height, 240, 2000, 700),
                         "dpr": _clamp(dpr, 1.0, 2.0, 2.0)}
        for sid in list(self.sessions.values()):
            with contextlib.suppress(Exception):
                await self._apply_metrics(sid)
        if self.session_id:
            with contextlib.suppress(Exception):
                await self.send("Page.stopScreencast", {}, self.session_id)
            await self._start_screencast()

    async def _broadcast_bytes(self, payload: bytes) -> None:
        for client in list(self.clients):
            with contextlib.suppress(Exception):
                await client.send_bytes(payload)

    async def broadcast(self, obj: dict) -> None:
        for client in list(self.clients):
            with contextlib.suppress(Exception):
                await client.send_str(json.dumps(obj))

    # ---- 入力 --------------------------------------------------------------
    async def handle_input(self, m: dict, client: Any) -> None:  # noqa: ANN401
        """操作画面からの入力を CDP へ送る。未知・不正な入力は無視する。"""
        kind = m.get("type")
        if kind == "viewport":
            await self.set_viewport(m.get("width"), m.get("height"), m.get("dpr"))
            return
        sid = self.session_id
        if not sid:
            return
        for params in input_to_cdp(m):
            if params[0] == "probe":
                r = await self.send("Runtime.evaluate", {
                    "expression": params[1], "returnByValue": True}, sid)
                await client.send_str(json.dumps({
                    "type": "probe", "seq": m.get("seq"),
                    "editable": r.get("result", {}).get("value")}))
            else:
                await self.send(params[0], params[1], sid)

    async def get_cookies(self) -> list[dict]:
        return (await self.send("Storage.getCookies"))["cookies"]


def input_to_cdp(m: dict) -> list[tuple[str, Any]]:
    """操作画面の入力 1 件を、CDP の呼び出しの列へ変換する(純粋関数、テスト用)。"""
    kind = m.get("type")
    calls: list[tuple[str, Any]] = []
    try:
        if kind == "touch":
            ev = TOUCH_TYPES[m["phase"]]
            points = [] if ev in ("touchEnd", "touchCancel") else [
                {"x": float(p["x"]), "y": float(p["y"]), "id": int(p.get("id", 0))}
                for p in m.get("points", [])[:5]]
            if ev in ("touchStart", "touchMove") and not points:
                return []
            calls.append(("Input.dispatchTouchEvent",
                          {"type": ev, "touchPoints": points}))
        elif kind == "mouse":
            ev = MOUSE_TYPES[m["phase"]]
            params: dict[str, Any] = {
                "type": ev, "x": float(m["x"]), "y": float(m["y"]),
                "button": "left", "clickCount": 1}
            if ev == "mouseMoved":
                params["button"] = "left" if m.get("pressed") else "none"
                params["buttons"] = 1 if m.get("pressed") else 0
            calls.append(("Input.dispatchMouseEvent", params))
        elif kind == "wheel":
            calls.append(("Input.dispatchMouseEvent", {
                "type": "mouseWheel", "x": float(m["x"]), "y": float(m["y"]),
                "deltaX": float(m.get("dx", 0)), "deltaY": float(m.get("dy", 0))}))
        elif kind == "probe":
            expr = f"{EDITABLE_AT}({float(m['x'])}, {float(m['y'])})"
            calls.append(("probe", expr))
        elif kind == "text":
            text = m.get("text")
            if isinstance(text, str) and text:
                calls.append(("Input.insertText", {"text": text[:10000]}))
        elif kind == "key" and m.get("key") in KEYS:
            key = {"key": m["key"], **KEYS[m["key"]]}
            calls.append(("Input.dispatchKeyEvent", {**key, "type": "keyDown"}))
            up = {k: v for k, v in key.items() if k != "text"}
            calls.append(("Input.dispatchKeyEvent", {**up, "type": "keyUp"}))
        elif kind == "nav":
            if m.get("action") == "back":
                calls.append(("Runtime.evaluate", {"expression": "history.back()"}))
            elif m.get("action") == "reload":
                calls.append(("Page.reload", {}))
    except (KeyError, TypeError, ValueError):
        return []
    return calls
