import sys
import tempfile
import time
import unittest
from pathlib import Path

from helpers import load_service


def ck(domain: str, name: str = "n", value: str = "v", **kw: object) -> dict:
    return {"domain": domain, "name": name, "value": value, "path": "/", "secure": True,
            "httpOnly": False, "session": False, "expires": 1900000000.5, **kw}


class FakeRuntime:
    novnc_port = 6080

    def __init__(self, cookies: list[dict] | None = None) -> None:
        self.cookies = cookies if cookies is not None else []
        self.started: list[str] = []
        self.stopped = 0
        self.fail_start = False
        self.fail_get = False

    def start(self, start_url: str) -> None:
        if self.fail_start:
            msg = "boom"
            raise RuntimeError(msg)
        self.started.append(start_url)

    def get_cookies(self) -> list[dict]:
        if self.fail_get:
            msg = "cdp down"
            raise RuntimeError(msg)
        return self.cookies

    def stop(self) -> None:
        self.stopped += 1


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
        cs = [ck(".co.jp", "a"), ck(".example.co.jp", "b")]
        kept = self.n.filter_cookies(cs, "https://www.example.co.jp/")
        self.assertEqual([c["name"] for c in kept], ["b"])

    def test_filter_ip_host(self) -> None:
        cs = [ck("127.0.0.1", "a"), ck("localhost", "b")]
        kept = self.n.filter_cookies(cs, "http://127.0.0.1:8099/login")
        self.assertEqual([c["name"] for c in kept], ["a"])

    def test_to_netscape(self) -> None:
        text = self.n.to_netscape([
            ck(".nicovideo.jp", "plain", "1", secure=False),
            ck("www.nicovideo.jp", "sess", "2", session=True, expires=-1),
            ck(".nicovideo.jp", "user_session", "3", httpOnly=True)])
        lines = text.splitlines()
        self.assertEqual(lines[0], "# Netscape HTTP Cookie File")
        rows = [ln.split("\t") for ln in lines[1:]]
        self.assertEqual(rows[0], [".nicovideo.jp", "TRUE", "/", "FALSE", "1900000000", "plain", "1"])
        self.assertEqual(rows[1], ["www.nicovideo.jp", "FALSE", "/", "TRUE", "0", "sess", "2"])
        self.assertEqual(rows[2][0], "#HttpOnly_.nicovideo.jp")
        self.assertTrue(text.endswith("\n"))


class SessionTest(unittest.TestCase):
    URL = "https://account.nicovideo.jp/login"

    def setUp(self) -> None:
        self.dir = tempfile.mkdtemp()
        self.m = load_service("browserServer", self.dir)
        self.rt = FakeRuntime([ck(".nicovideo.jp", "user_session", "TOPSECRETVALUE", httpOnly=True),
                               ck(".google.com", "SID", "OTHER")])
        self.mgr = sys.modules["session"].SessionManager(self.rt, timeout=3600)
        self.addCleanup(self.mgr._teardown)
        self.SessionError = sys.modules["session"].SessionError

    def store(self, name: str = "nico") -> Path:
        return Path(self.dir) / f"{name}.txt"

    def test_start_validation(self) -> None:
        for profile, url in (("../x", self.URL), ("", self.URL), (None, self.URL), ("a", "ftp://x/"),
                             ("a", "javascript:alert(1)"), ("a", ""), ("a", None), ("a", "http://")):
            with self.assertRaises(self.SessionError, msg=(profile, url)) as cm:
                self.mgr.start(profile, url)
            self.assertEqual(cm.exception.status, 400)
        self.assertEqual(self.mgr.info()["state"], "idle")
        self.assertEqual(self.rt.started, [])

    def test_commit_saves_only_site_cookies(self) -> None:
        info = self.mgr.start("nico", self.URL)
        self.assertEqual((info["state"], info["profile"]), ("running", "nico"))
        self.assertEqual(self.rt.started, [self.URL])
        res = self.mgr.commit()
        self.assertEqual(res, {"profile": "nico", "saved": 1, "domain": "nicovideo.jp"})
        text = self.store().read_text()
        self.assertIn("#HttpOnly_.nicovideo.jp\tTRUE\t/\tTRUE", text)
        self.assertNotIn("google", text)
        self.assertEqual(oct(self.store().stat().st_mode & 0o777), "0o600")
        # 保存後はブラウザを破棄して idle。応答に cookie の値は含まれない
        self.assertEqual((self.mgr.info()["state"], self.rt.stopped), ("idle", 1))
        self.assertNotIn("TOPSECRETVALUE", str(res))

    def test_update_overwrites_existing_profile(self) -> None:
        self.store().write_text("OLD")
        self.mgr.start("nico", self.URL)
        self.mgr.commit()
        self.assertNotIn("OLD", self.store().read_text())

    def test_only_one_session(self) -> None:
        self.mgr.start("nico", self.URL)
        with self.assertRaises(self.SessionError) as cm:
            self.mgr.start("other", self.URL)
        self.assertEqual(cm.exception.status, 409)
        self.assertEqual(self.mgr.info()["profile"], "nico")

    def test_commit_without_session(self) -> None:
        with self.assertRaises(self.SessionError) as cm:
            self.mgr.commit()
        self.assertEqual(cm.exception.status, 409)

    def test_commit_without_cookies_keeps_browser(self) -> None:
        self.rt.cookies = [ck(".google.com", "SID")]
        self.mgr.start("nico", self.URL)
        with self.assertRaises(self.SessionError) as cm:
            self.mgr.commit()
        self.assertEqual(cm.exception.status, 422)
        self.assertFalse(self.store().exists())
        # まだログイン前でも、ブラウザは残り、続けて操作・再度保存できる
        self.assertEqual((self.mgr.info()["state"], self.rt.stopped), ("running", 0))
        self.rt.cookies = [ck(".nicovideo.jp", "user_session")]
        self.assertEqual(self.mgr.commit()["saved"], 1)

    def test_commit_error_tears_down(self) -> None:
        self.rt.fail_get = True
        self.mgr.start("nico", self.URL)
        with self.assertRaises(self.SessionError) as cm:
            self.mgr.commit()
        self.assertEqual(cm.exception.status, 500)
        self.assertEqual((self.mgr.info()["state"], self.rt.stopped), ("idle", 1))
        self.assertFalse(self.store().exists())

    def test_start_failure_returns_to_idle(self) -> None:
        self.rt.fail_start = True
        with self.assertRaises(self.SessionError) as cm:
            self.mgr.start("nico", self.URL)
        self.assertEqual(cm.exception.status, 500)
        self.assertEqual((self.mgr.info()["state"], self.rt.stopped), ("idle", 1))
        self.rt.fail_start = False
        self.mgr.start("nico", self.URL)  # 失敗後も再開できる

    def test_cancel_saves_nothing(self) -> None:
        self.mgr.start("nico", self.URL)
        self.mgr.cancel()
        self.assertEqual((self.mgr.info()["state"], self.rt.stopped), ("idle", 1))
        self.assertFalse(self.store().exists())

    def test_timeout_tears_down(self) -> None:
        self.mgr.start("nico", self.URL)
        self.mgr._expire()
        self.assertEqual((self.mgr.info()["state"], self.rt.stopped), ("idle", 1))

    def test_timeout_fires_via_timer(self) -> None:
        mgr = sys.modules["session"].SessionManager(self.rt, timeout=0.2)
        self.addCleanup(mgr._teardown)
        mgr.start("nico", self.URL)
        for _ in range(30):
            if mgr.info()["state"] == "idle":
                break
            time.sleep(0.1)
        self.assertEqual(mgr.info()["state"], "idle")

    def test_expire_ignored_while_committing(self) -> None:
        self.mgr.start("nico", self.URL)
        self.mgr._state = "committing"
        self.mgr._expire()
        self.assertEqual((self.mgr.info()["state"], self.rt.stopped), ("committing", 0))
        with self.assertRaises(self.SessionError):
            self.mgr.cancel()

    def test_info_remaining(self) -> None:
        self.assertEqual(self.mgr.info()["remaining"], 0)
        self.mgr.start("nico", self.URL)
        self.assertTrue(3590 <= self.mgr.info()["remaining"] <= 3600)


class ApiTest(unittest.TestCase):
    def setUp(self) -> None:
        self.dir = tempfile.mkdtemp()
        self.m = load_service("browserServer", self.dir)
        self.rt = FakeRuntime([ck(".nicovideo.jp", "user_session", "TOPSECRETVALUE")])
        self.m.manager = sys.modules["session"].SessionManager(self.rt, timeout=3600)
        self.addCleanup(self.m.manager._teardown)
        self.client = self.m.app.test_client()
        self.body = {"profile": "nico", "start_url": "https://account.nicovideo.jp/login"}

    def test_index_page(self) -> None:
        r = self.client.get("/")
        self.assertEqual(r.status_code, 200)
        self.assertIn("text/html", r.content_type)
        self.assertIn("cookie を保存", r.get_data(as_text=True))

    def test_full_flow(self) -> None:
        self.assertEqual(self.client.get("/session").json["state"], "idle")
        self.assertEqual(self.client.post("/session", json=self.body).status_code, 200)
        self.assertEqual(self.client.get("/session").json["state"], "running")
        self.assertEqual(self.client.post("/session", json=self.body).status_code, 409)
        r = self.client.post("/session/commit")
        self.assertEqual((r.status_code, r.json["saved"]), (200, 1))
        self.assertNotIn("TOPSECRETVALUE", r.get_data(as_text=True))
        self.assertTrue((Path(self.dir) / "nico.txt").exists())
        self.assertEqual(self.client.get("/session").json["state"], "idle")

    def test_errors(self) -> None:
        self.assertEqual(self.client.post("/session", data="x").status_code, 400)
        self.assertEqual(self.client.post("/session", json=[1]).status_code, 400)
        r = self.client.post("/session", json={**self.body, "profile": "../x"})
        self.assertEqual(r.status_code, 400)
        self.assertEqual(self.client.post("/session/commit").status_code, 409)
        self.client.post("/session", json=self.body)
        self.rt.cookies = []
        self.assertEqual(self.client.post("/session/commit").status_code, 422)
        self.assertEqual(self.client.delete("/session").status_code, 200)
        self.assertEqual(self.client.get("/session").json["state"], "idle")
        self.assertFalse((Path(self.dir) / "nico.txt").exists())


if __name__ == "__main__":
    unittest.main()
