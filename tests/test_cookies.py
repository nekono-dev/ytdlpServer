import os
import tempfile
import time
import unittest
from pathlib import Path

from helpers import ROOT, FakeRedis, load_service

NICO_ERR = (
    "ERROR: [niconico] sm46845740: Sensitive content, login required. Use --cookies, "
    "--cookies-from-browser, --username and --password, --netrc-cmd, or --netrc "
    "(niconico) to provide account credentials. See  https://github.com/yt-dlp/yt-dlp/wiki/FAQ")


class CookiesTest(unittest.TestCase):
    def setUp(self) -> None:
        self.dir = tempfile.mkdtemp()
        self.c = load_service("apiServer", self.dir).cookies

    def put(self, name: str = "nico", body: bytes = b"# Netscape\nA\n") -> Path:
        p = Path(self.dir) / f"{name}.txt"
        p.write_bytes(body)
        return p

    def test_api_worker_copies_identical(self) -> None:
        for name in ("cookies.py", "presets.json"):
            a = (ROOT / "apiServer/src" / name).read_bytes()
            for other in ("workerServer", "browserServer"):
                self.assertEqual(
                    a, (ROOT / other / "src" / name).read_bytes(),
                    f"{name} は全アプリで同一に保つこと({other})")

    def test_classify(self) -> None:
        for ok in (NICO_ERR, "ERROR: [niconico] sm1: Invalid session, re-login required",
                   "Sign in to confirm you're not a bot",
                   "Use --cookies-from-browser or --cookies for the authentication",
                   "The provided YouTube account cookies are no longer valid",
                   f"{self.c.MISSING_PREFIX}: x"):
            self.assertTrue(self.c.classify_login_required(ok), ok)
        for ng in ("HTTP Error 404: Not Found", "Unsupported URL", "", None):
            self.assertFalse(self.c.classify_login_required(ng), ng)

    def test_check_options(self) -> None:
        for bad in (["--cookies", "x"], ["--cookies=x"], ["--cookies-from-browser", "chrome"],
                    ["-u", "a"], ["-uname"], ["-p", "x"], ["--username=a"], ["--password", "x"],
                    ["--netrc"], ["--netrc-cmd", "id"], ["-n"], ["--twofactor", "1"]):
            with self.assertRaises(ValueError, msg=bad):
                self.c.check_options(bad)
        # 通常のオプションや "-" 始まりの値は許可される
        self.c.check_options(["-f", "bestaudio", "-x", "--playlist-items", "-3:", "-P", "/x",
                              "--extractor-args", "youtube:a=b", "--video-password", "x"])

    def test_error_message_hides_value(self) -> None:
        with self.assertRaises(ValueError) as cm:
            self.c.check_options(["--password=TOPSECRET"])
        self.assertNotIn("TOPSECRET", str(cm.exception))

    def test_validate_profile(self) -> None:
        self.assertEqual(self.c.validate_profile("nico_1-a"), "nico_1-a")
        for bad in ("", "../x", "a/b", "a b", "x" * 65, None, 1):
            with self.assertRaises(ValueError, msg=bad):
                self.c.validate_profile(bad)

    def test_mask_command(self) -> None:
        cmd = ["yt-dlp", "--cookies", "/tmp/secret.txt", "url"]
        self.assertEqual(self.c.mask_command(cmd), ["yt-dlp", "--cookies", "<cookies>", "url"])

    def test_cookie_file_none(self) -> None:
        with self.c.cookie_file(None) as p:
            self.assertIsNone(p)

    def test_cookie_file_missing(self) -> None:
        with self.assertRaises(self.c.ProfileNotFoundError), self.c.cookie_file("nope"):
            pass

    def test_cookie_file_writeback_and_cleanup(self) -> None:
        store = self.put()
        with self.c.cookie_file("nico") as tmp:
            self.assertNotEqual(tmp, str(store))
            self.assertEqual(oct(Path(tmp).stat().st_mode & 0o777), "0o600")
            Path(tmp).write_bytes(b"# Netscape\nUPDATED\n")  # yt-dlp による更新を模擬
        self.assertEqual(store.read_bytes(), b"# Netscape\nUPDATED\n")
        self.assertEqual(oct(store.stat().st_mode & 0o777), "0o600")
        self.assertFalse(Path(tmp).exists(), "一時ファイルは削除される")
        self.assertEqual([p.name for p in Path(self.dir).iterdir()], ["nico.txt"])

    def test_cookie_file_writeback_even_on_failure(self) -> None:
        store = self.put()
        with self.assertRaises(RuntimeError), self.c.cookie_file("nico") as tmp:
            Path(tmp).write_bytes(b"NEW")
            raise RuntimeError
        self.assertEqual(store.read_bytes(), b"NEW")

    def test_cookie_file_unchanged_keeps_mtime(self) -> None:
        store = self.put()
        os.utime(store, (1000, 1000))
        with self.c.cookie_file("nico"):
            pass
        self.assertEqual(store.stat().st_mtime, 1000)

    def test_profile_state(self) -> None:
        r = FakeRedis()
        self.assertEqual(self.c.profile_state(r, "nico"), "missing")
        store = self.put()
        os.utime(store, (1000, 1000))
        self.assertEqual(self.c.profile_state(r, "nico"), "valid")
        self.c.mark_expired(r, "nico")
        self.assertEqual(self.c.profile_state(r, "nico"), "expired")
        # cookie を入れ直す(失効記録より新しい mtime)と自動で valid に戻る
        os.utime(store, (time.time() + 10, time.time() + 10))
        self.assertEqual(self.c.profile_state(r, "nico"), "valid")
        self.assertEqual(self.c.profile_state(None, "nico"), "valid")

    def test_list_profiles_hides_content(self) -> None:
        self.put(body=b"# Netscape\nTOPSECRETVALUE\n")
        (Path(self.dir) / "bad name.txt").write_text("x")
        res = self.c.list_profiles(FakeRedis())
        self.assertEqual([x["profile"] for x in res], ["nico"])
        self.assertNotIn("TOPSECRETVALUE", str(res))

    def test_login_url(self) -> None:
        self.assertIsNone(self.c.login_url("a"))
        self.c.BROWSER_UI_URL = "http://h:6080"
        self.assertEqual(self.c.login_url("a b"), "http://h:6080/?profile=a%20b")

    def test_login_url_with_start_url(self) -> None:
        from urllib.parse import parse_qs, urlsplit
        self.c.BROWSER_UI_URL = "http://h:8080"
        url = self.c.login_url("nico", "https://www.nicovideo.jp/watch/sm1?x=1#f")
        q = parse_qs(urlsplit(url).query)
        self.assertEqual(q, {"profile": ["nico"], "start_url": ["https://www.nicovideo.jp/"]})
        # プロファイル未指定でも開始 URL は付く。URL が http(s) でなければ付けない
        self.assertEqual(parse_qs(urlsplit(self.c.login_url(None, "http://a:81/x")).query),
                         {"start_url": ["http://a:81/"]})
        self.assertEqual(self.c.login_url(None, "file:///etc/passwd"), "http://h:8080/")
        self.assertEqual(self.c.login_url("a", "not a url"), "http://h:8080/?profile=a")

    def test_save_profile(self) -> None:
        dst = self.c.save_profile("nico", b"ONE")
        self.assertEqual((dst.read_bytes(), oct(dst.stat().st_mode & 0o777)), (b"ONE", "0o600"))
        # 以前の一時ファイルが緩い権限で残っていても、保存後は 0600 で、一時ファイルは残らない
        stale = Path(self.dir) / ".nico.txt.tmp"
        stale.write_bytes(b"x")
        stale.chmod(0o644)
        self.c.save_profile("nico", b"TWO")
        self.assertEqual(dst.read_bytes(), b"TWO")
        self.assertEqual(oct(dst.stat().st_mode & 0o777), "0o600")
        self.assertEqual([p.name for p in Path(self.dir).iterdir()], ["nico.txt"])
        with self.assertRaises(ValueError):
            self.c.save_profile("../x", b"x")


class ProfileEntriesTest(unittest.TestCase):
    def setUp(self) -> None:
        self.dir = tempfile.mkdtemp()
        self.c = load_service("apiServer", self.dir).cookies

    def test_presets(self) -> None:
        names = [e["name"] for e in self.c.load_presets()]
        self.assertEqual(names, ["youtube", "niconico", "instagram", "x", "bilibili"])
        for e in self.c.load_presets():
            self.assertTrue(e["preset"])
            self.assertTrue(e["start_url"].startswith("https://"), e)
            # 開始 URL のホストは、保存対象のドメインに含まれる(ログイン画面の cookie も保存する)
            from urllib.parse import urlsplit
            host = urlsplit(e["start_url"]).hostname
            self.assertTrue(self.c.host_matches(host, e["domains"]), e)

    def test_resolve_profile(self) -> None:
        for url, want in (("https://www.youtube.com/watch?v=x", "youtube"),
                          ("https://youtu.be/x", "youtube"),
                          ("https://m.youtube.com/shorts/x", "youtube"),
                          ("https://www.nicovideo.jp/watch/sm9", "niconico"),
                          ("https://nico.ms/sm9", "niconico"),
                          ("https://twitter.com/a/status/1", "x"),
                          ("https://x.com/a/status/1", "x"),
                          ("https://www.instagram.com/reel/x/", "instagram"),
                          ("https://www.bilibili.com/video/BV1", "bilibili"),
                          ("https://vimeo.com/1", None),
                          ("https://notyoutube.com/", None),
                          ("not a url", None), (None, None)):
            self.assertEqual(self.c.resolve_profile(url), want, url)

    def test_history_add_resolve_remove(self) -> None:
        e = self.c.add_history("vimeo", "https://vimeo.com/log_in", ["vimeo.com"])
        self.assertEqual((e["label"], e["preset"]), ("vimeo", False))
        hist = Path(self.dir) / "profiles.json"
        self.assertEqual(oct(hist.stat().st_mode & 0o777), "0o600")
        self.assertEqual(self.c.resolve_profile("https://player.vimeo.com/video/1"), "vimeo")
        self.assertEqual([x["name"] for x in self.c.profile_entries()][-1], "vimeo")
        # 同名(プリセット・履歴)は追加できない
        for name in ("vimeo", "youtube"):
            with self.assertRaises(ValueError):
                self.c.add_history(name, "https://a.example/", ["a.example"])
        with self.assertRaises(ValueError):
            self.c.add_history("bad", "ftp://a/", ["a"])
        # 削除すると cookie も消える。プリセットは削除できない
        (Path(self.dir) / "vimeo.txt").write_text("x")
        self.c.remove_history("vimeo")
        self.assertFalse((Path(self.dir) / "vimeo.txt").exists())
        self.assertIsNone(self.c.resolve_profile("https://vimeo.com/1"))
        with self.assertRaises(KeyError):
            self.c.remove_history("vimeo")
        with self.assertRaises(ValueError):
            self.c.remove_history("youtube")
        self.assertEqual([p.name for p in Path(self.dir).iterdir()], ["profiles.json"])

    def test_broken_history_is_ignored(self) -> None:
        (Path(self.dir) / "profiles.json").write_text("{broken")
        self.assertEqual(len(self.c.profile_entries()), 5)
        (Path(self.dir) / "profiles.json").write_text(
            '{"profiles": [{"name": "../x", "start_url": "https://a/", "domains": ["a"]},'
            ' {"name": "ok", "start_url": "https://b.example/", "domains": ["b.example"]}]}')
        self.assertEqual([e["name"] for e in self.c.load_history()], ["ok"])

    def test_list_profiles_has_label(self) -> None:
        (Path(self.dir) / "niconico.txt").write_text("x")
        (Path(self.dir) / "legacy.txt").write_text("x")
        res = {r["profile"]: r for r in self.c.list_profiles(None)}
        self.assertEqual((res["niconico"]["label"], res["niconico"]["domains"][0]), ("ニコニコ", "nicovideo.jp"))
        self.assertEqual((res["legacy"]["label"], res["legacy"]["domains"]), ("legacy", []))


if __name__ == "__main__":
    unittest.main()
