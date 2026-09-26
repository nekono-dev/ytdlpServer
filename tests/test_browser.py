import asyncio
import json
import sys
import tempfile
import unittest
from pathlib import Path

from aiohttp.test_utils import TestClient, TestServer
from helpers import load_service


def ck(domain: str, name: str = "n", value: str = "v", **kw: object) -> dict:
    return {"domain": domain, "name": name, "value": value, "path": "/", "secure": True,
            "httpOnly": False, "session": False, "expires": 1900000000.5, **kw}


class FakeBrowser:
    """ChromiumBrowser の代わり。起動・cookie・入力を記録する。"""

    instances: list["FakeBrowser"] = []
    cookies: list[dict] = []
    fail_start = False
    fail_get = False

    def __init__(self, *, mobile: bool) -> None:
        self.mobile = mobile
        self.clients: set = set()
        self.url = ""
        self.started: list[str] = []
        self.stopped = 0
        self.inputs: list[dict] = []
        FakeBrowser.instances.append(self)

    async def start(self, start_url: str) -> None:
        if FakeBrowser.fail_start:
            raise RuntimeError("boom")
        self.started.append(start_url)

    async def get_cookies(self) -> list[dict]:
        if FakeBrowser.fail_get:
            raise RuntimeError("cdp down")
        return FakeBrowser.cookies

    async def stop(self) -> None:
        self.stopped += 1

    async def handle_input(self, m: dict, client: object) -> None:
        self.inputs.append(m)
        if m.get("type") == "probe":
            await client.send_str(json.dumps({"type": "probe", "seq": m.get("seq"), "editable": True}))


def reset_fake() -> None:
    FakeBrowser.instances = []
    FakeBrowser.cookies = [ck(".nicovideo.jp", "user_session", "TOPSECRETVALUE", httpOnly=True),
                           ck(".google.com", "SID", "OTHER")]
    FakeBrowser.fail_start = FakeBrowser.fail_get = False


class NetscapeTest(unittest.TestCase):
    def setUp(self) -> None:
        load_service("browserServer", tempfile.mkdtemp())
        self.n = sys.modules["netscape"]

    def test_site_domain(self) -> None:
        for url, want in (("https://account.nicovideo.jp/login", "nicovideo.jp"),
                          ("https://www.example.co.jp/", "example.co.jp"),
                          ("http://127.0.0.1:8099/login", "127.0.0.1"),
                          ("http://localhost:3000/", "localhost")):
            self.assertEqual(self.n.site_domain(url), want, url)

    def test_filter_keeps_only_site(self) -> None:
        cs = [ck(".nicovideo.jp", "a"), ck("www.nicovideo.jp", "b"), ck("nicovideo.jp", "c"),
              ck(".google.com", "d"), ck("accounts.google.com", "e"),
              ck("evilnicovideo.jp", "f"), ck(".jp", "g"), ck("127.0.0.1", "h")]
        kept = self.n.filter_cookies(cs, "https://account.nicovideo.jp/login")
        self.assertEqual([c["name"] for c in kept], ["a", "b", "c"])

    def test_filter_public_suffix_not_kept(self) -> None:
        kept = self.n.filter_cookies([ck(".co.jp", "a"), ck(".example.co.jp", "b")],
                                     "https://www.example.co.jp/")
        self.assertEqual([c["name"] for c in kept], ["b"])

    def test_to_netscape(self) -> None:
        text = self.n.to_netscape([
            ck(".nicovideo.jp", "plain", "1", secure=False),
            ck("www.nicovideo.jp", "sess", "2", session=True, expires=-1),
            ck(".nicovideo.jp", "user_session", "3", httpOnly=True)])
        rows = [ln.split("\t") for ln in text.splitlines()[1:]]
        self.assertEqual(text.splitlines()[0], "# Netscape HTTP Cookie File")
        self.assertEqual(rows[0], [".nicovideo.jp", "TRUE", "/", "FALSE", "1900000000", "plain", "1"])
        self.assertEqual(rows[1], ["www.nicovideo.jp", "FALSE", "/", "TRUE", "0", "sess", "2"])
        self.assertEqual(rows[2][0], "#HttpOnly_.nicovideo.jp")


class InputTest(unittest.TestCase):
    """操作画面の入力 → CDP 呼び出しの変換。"""

    def setUp(self) -> None:
        load_service("browserServer", tempfile.mkdtemp())
        self.conv = sys.modules["browser"].input_to_cdp

    def test_touch(self) -> None:
        start = self.conv({"type": "touch", "phase": "start", "points": [{"id": 3, "x": "1.5", "y": 2}]})
        self.assertEqual(start, [("Input.dispatchTouchEvent",
                                  {"type": "touchStart", "touchPoints": [{"x": 1.5, "y": 2.0, "id": 3}]})])
        end = self.conv({"type": "touch", "phase": "end", "points": [{"x": 1, "y": 2}]})
        self.assertEqual(end[0][1], {"type": "touchEnd", "touchPoints": []})
        self.assertEqual(self.conv({"type": "touch", "phase": "move", "points": []}), [])
        many = self.conv({"type": "touch", "phase": "move", "points": [{"x": i, "y": i} for i in range(9)]})
        self.assertEqual(len(many[0][1]["touchPoints"]), 5)

    def test_mouse(self) -> None:
        down = self.conv({"type": "mouse", "phase": "down", "x": 1, "y": 2})[0][1]
        self.assertEqual((down["type"], down["button"], down["clickCount"]), ("mousePressed", "left", 1))
        drag = self.conv({"type": "mouse", "phase": "move", "x": 1, "y": 2, "pressed": True})[0][1]
        self.assertEqual((drag["type"], drag["buttons"]), ("mouseMoved", 1))
        hover = self.conv({"type": "mouse", "phase": "move", "x": 1, "y": 2})[0][1]
        self.assertEqual((hover["button"], hover["buttons"]), ("none", 0))

    def test_keys_and_text(self) -> None:
        enter = self.conv({"type": "key", "key": "Enter"})
        self.assertEqual([c[1]["type"] for c in enter], ["keyDown", "keyUp"])
        self.assertEqual(enter[0][1]["text"], "\r")
        self.assertNotIn("text", enter[1][1])
        self.assertEqual(self.conv({"type": "key", "key": "F12"}), [])
        self.assertEqual(self.conv({"type": "text", "text": "日本語"}),
                         [("Input.insertText", {"text": "日本語"})])
        self.assertEqual(self.conv({"type": "text", "text": ""}), [])
        self.assertEqual(self.conv({"type": "text", "text": 5}), [])

    def test_probe_and_nav_and_invalid(self) -> None:
        probe = self.conv({"type": "probe", "x": 10, "y": 20})
        self.assertEqual(probe[0][0], "probe")
        self.assertTrue(probe[0][1].endswith("(10.0, 20.0)"))
        # 座標は数値に変換されるため、式への文字列の注入はできない
        self.assertEqual(self.conv({"type": "probe", "x": "1);alert(1", "y": 0}), [])
        self.assertEqual(self.conv({"type": "nav", "action": "back"})[0][1], {"expression": "history.back()"})
        self.assertEqual(self.conv({"type": "nav", "action": "reload"}), [("Page.reload", {})])
        for bad in ({"type": "touch", "phase": "x"}, {"type": "mouse", "phase": "down"}, {"type": "zzz"}, {}):
            self.assertEqual(self.conv(bad), [], bad)


class SessionTest(unittest.IsolatedAsyncioTestCase):
    URL = "https://account.nicovideo.jp/login"

    def setUp(self) -> None:
        self.dir = tempfile.mkdtemp()
        load_service("browserServer", self.dir)
        self.s = sys.modules["session"]
        reset_fake()
        self.mgr = self.s.SessionManager(FakeBrowser, timeout=3600)

    async def asyncTearDown(self) -> None:
        await self.mgr.cancel()

    def store(self) -> Path:
        return Path(self.dir) / "nico.txt"

    async def test_start_validation(self) -> None:
        for profile, url in (("../x", self.URL), ("", self.URL), (None, self.URL), ("a", "ftp://x/"),
                             ("a", "javascript:alert(1)"), ("a", ""), ("a", None), ("a", "http://")):
            with self.assertRaises(self.s.SessionError, msg=(profile, url)) as cm:
                await self.mgr.start(profile, url)
            self.assertEqual(cm.exception.status, 400)
        self.assertEqual((self.mgr.state, FakeBrowser.instances), ("idle", []))

    async def test_commit_saves_only_site_cookies(self) -> None:
        info = await self.mgr.start("nico", self.URL, mobile=True)
        self.assertEqual((info["state"], info["profile"], info["mobile"]), ("running", "nico", True))
        b = FakeBrowser.instances[0]
        self.assertEqual((b.started, b.mobile), ([self.URL], True))
        res = await self.mgr.commit()
        self.assertEqual(res, {"profile": "nico", "saved": 1, "domains": ["nicovideo.jp"]})
        text = self.store().read_text()
        self.assertIn("#HttpOnly_.nicovideo.jp\tTRUE\t/\tTRUE", text)
        self.assertNotIn("google", text)
        self.assertEqual(oct(self.store().stat().st_mode & 0o777), "0o600")
        self.assertEqual((self.mgr.state, b.stopped, self.mgr.browser), ("idle", 1, None))
        self.assertNotIn("TOPSECRETVALUE", str(res))

    async def test_only_one_session(self) -> None:
        await self.mgr.start("nico", self.URL)
        with self.assertRaises(self.s.SessionError) as cm:
            await self.mgr.start("other", self.URL)
        self.assertEqual(cm.exception.status, 409)
        self.assertEqual(self.mgr.info()["profile"], "nico")

    async def test_commit_without_session(self) -> None:
        with self.assertRaises(self.s.SessionError) as cm:
            await self.mgr.commit()
        self.assertEqual(cm.exception.status, 409)

    async def test_commit_without_cookies_keeps_browser(self) -> None:
        FakeBrowser.cookies = [ck(".google.com", "SID")]
        await self.mgr.start("nico", self.URL)
        with self.assertRaises(self.s.SessionError) as cm:
            await self.mgr.commit()
        self.assertEqual(cm.exception.status, 422)
        self.assertEqual((self.mgr.state, FakeBrowser.instances[0].stopped), ("running", 0))
        FakeBrowser.cookies = [ck(".nicovideo.jp", "user_session")]
        self.assertEqual((await self.mgr.commit())["saved"], 1)

    async def test_commit_error_tears_down(self) -> None:
        FakeBrowser.fail_get = True
        await self.mgr.start("nico", self.URL)
        with self.assertRaises(self.s.SessionError) as cm:
            await self.mgr.commit()
        self.assertEqual(cm.exception.status, 500)
        self.assertEqual((self.mgr.state, FakeBrowser.instances[0].stopped), ("idle", 1))
        self.assertFalse(self.store().exists())

    async def test_start_failure_returns_to_idle(self) -> None:
        FakeBrowser.fail_start = True
        with self.assertRaises(self.s.SessionError) as cm:
            await self.mgr.start("nico", self.URL)
        self.assertEqual(cm.exception.status, 500)
        self.assertEqual((self.mgr.state, FakeBrowser.instances[0].stopped), ("idle", 1))
        FakeBrowser.fail_start = False
        await self.mgr.start("nico", self.URL)

    async def test_cancel_saves_nothing(self) -> None:
        await self.mgr.start("nico", self.URL)
        await self.mgr.cancel()
        self.assertEqual((self.mgr.state, FakeBrowser.instances[0].stopped), ("idle", 1))
        self.assertFalse(self.store().exists())

    async def test_timeout_tears_down(self) -> None:
        mgr = self.s.SessionManager(FakeBrowser, timeout=0.2)
        await mgr.start("nico", self.URL)
        for _ in range(30):
            if mgr.state == "idle":
                break
            await asyncio.sleep(0.05)
        self.assertEqual((mgr.state, FakeBrowser.instances[0].stopped), ("idle", 1))

    async def test_cancel_while_committing(self) -> None:
        await self.mgr.start("nico", self.URL)
        self.mgr._state = "committing"
        with self.assertRaises(self.s.SessionError):
            await self.mgr.cancel()
        self.mgr._state = "running"


class ApiTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.dir = tempfile.mkdtemp()
        self.m = load_service("browserServer", self.dir)
        reset_fake()
        self.mgr = sys.modules["session"].SessionManager(FakeBrowser, timeout=3600)
        self.client = TestClient(TestServer(self.m.create_app(self.mgr)))
        await self.client.start_server()
        self.body = {"profile": "nico", "start_url": "https://account.nicovideo.jp/login", "mobile": True}

    async def asyncTearDown(self) -> None:
        await self.mgr.cancel()
        await self.client.close()

    async def test_index_page(self) -> None:
        r = await self.client.get("/")
        self.assertEqual(r.status, 200)
        self.assertIn("text/html", r.content_type)
        self.assertIn("保存", await r.text())

    async def test_full_flow(self) -> None:
        self.assertEqual((await (await self.client.get("/session")).json())["state"], "idle")
        self.assertEqual((await self.client.post("/session", json=self.body)).status, 200)
        self.assertTrue(FakeBrowser.instances[0].mobile)
        self.assertEqual((await self.client.post("/session", json=self.body)).status, 409)
        r = await self.client.post("/session/commit")
        self.assertEqual(r.status, 200)
        text = await r.text()
        self.assertEqual(json.loads(text)["saved"], 1)
        self.assertNotIn("TOPSECRETVALUE", text)
        self.assertTrue((Path(self.dir) / "nico.txt").exists())

    async def test_errors(self) -> None:
        self.assertEqual((await self.client.post("/session", data="x")).status, 400)
        self.assertEqual((await self.client.post("/session", json=[1])).status, 400)
        self.assertEqual((await self.client.post("/session", json={**self.body, "profile": "../x"})).status, 400)
        self.assertEqual((await self.client.post("/session/commit")).status, 409)
        await self.client.post("/session", json={**self.body, "mobile": "yes"})
        self.assertFalse(FakeBrowser.instances[0].mobile, "mobile は true のときだけ有効")
        FakeBrowser.cookies = []
        r = await self.client.post("/session/commit")
        self.assertEqual(r.status, 422)
        self.assertIn("cookie がありません", (await r.json())["message"])
        self.assertEqual((await self.client.delete("/session")).status, 200)
        self.assertFalse((Path(self.dir) / "nico.txt").exists())

    async def test_websocket(self) -> None:
        ws = await self.client.ws_connect("/ws")
        self.assertTrue((await ws.receive()).type.name in ("CLOSE", "CLOSED"), "実行中でなければ閉じる")
        await self.client.post("/session", json=self.body)
        ws = await self.client.ws_connect("/ws")
        self.assertEqual(json.loads((await ws.receive()).data)["type"], "url")
        await ws.send_str(json.dumps({"type": "probe", "seq": 7, "x": 1, "y": 2}))
        reply = json.loads((await ws.receive()).data)
        self.assertEqual((reply["type"], reply["seq"]), ("probe", 7))
        await ws.send_str("not json")      # 不正な入力で接続が切れない
        await ws.send_str(json.dumps({"type": "text", "text": "a"}))
        await asyncio.sleep(0.1)
        self.assertEqual(FakeBrowser.instances[0].inputs[-1], {"type": "text", "text": "a"})
        self.assertEqual(len(FakeBrowser.instances[0].clients), 1)
        await ws.close()


if __name__ == "__main__":
    unittest.main()
