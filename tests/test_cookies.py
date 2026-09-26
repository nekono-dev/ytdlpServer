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
        a = (ROOT / "apiServer/src/cookies.py").read_bytes()
        w = (ROOT / "workerServer/src/cookies.py").read_bytes()
        self.assertEqual(a, w, "cookies.py は API/Worker で同一に保つこと")

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


if __name__ == "__main__":
    unittest.main()
