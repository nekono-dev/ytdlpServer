import os
import subprocess
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from helpers import FakeRedis, load_service

LOGIN_ERR = "ERROR: Sensitive content, login required. Use --cookies, --cookies-from-browser ..."


class WorkerTest(unittest.TestCase):
    def setUp(self) -> None:
        self.dir = tempfile.mkdtemp()
        self.redis = FakeRedis()
        self.m = load_service("workerServer", self.dir, self.redis)
        self.m.redis_client = self.redis
        self.c = self.m.cookies
        self.store = Path(self.dir) / "nico.txt"
        self.store.write_bytes(b"# Netscape\nTOPSECRETVALUE\n")
        os.environ["DOWNLOAD_DIR"] = self.dir

    def run_job(self, run: mock.Mock, **job: object) -> tuple[bool, str]:
        import function
        function.SAVEDIR = Path(self.dir)
        with mock.patch("subprocess.run", run):
            return function.run_yt_dlp({
                "url": "https://x/1", "options": [], "filename": "f", "id": "i", **job})

    def test_cmd_has_cookies_and_log_is_masked(self) -> None:
        run = mock.Mock(return_value=mock.Mock(stdout="ok"))
        with mock.patch("builtins.print") as pr:
            ok, _ = self.run_job(run, auth_profile="nico")
        self.assertTrue(ok)
        cmd = run.call_args.args[0]
        tmp = cmd[cmd.index("--cookies") + 1]
        self.assertNotEqual(tmp, str(self.store))
        logged = " ".join(str(a) for c in pr.call_args_list for a in c.args)
        self.assertNotIn(tmp, logged)
        self.assertIn("<cookies>", logged)

    def test_no_profile_no_cookies_flag(self) -> None:
        run = mock.Mock(return_value=mock.Mock(stdout="ok"))
        self.run_job(run)
        self.assertNotIn("--cookies", run.call_args.args[0])

    def test_missing_profile_fails_as_login_required(self) -> None:
        ok, out = self.run_job(mock.Mock(), auth_profile="ghost")
        self.assertFalse(ok)
        self.assertTrue(self.c.classify_login_required(out))

    def test_failure_still_writes_back_cookies(self) -> None:
        def fail(cmd, **_kw):  # noqa: ANN001, ANN202
            Path(cmd[cmd.index("--cookies") + 1]).write_bytes(b"REFRESHED")
            raise subprocess.CalledProcessError(1, cmd, stderr="boom")
        ok, out = self.run_job(mock.Mock(side_effect=fail), auth_profile="nico")
        self.assertEqual((ok, out), (False, "boom"))
        self.assertEqual(self.store.read_bytes(), b"REFRESHED")

    def make_failed(self, jid: str, **fields: str) -> str:
        key = f"ytdlp:jobs:in_progress:{jid}"
        self.redis.hset(key, mapping={"failed_count": "0", **fields})
        return key

    def test_record_failure_login_required(self) -> None:
        key = self.make_failed("j1", auth_profile="nico")
        new = self.m.record_failure(key, "nico", LOGIN_ERR)
        h = self.redis.hgetall(new)
        self.assertEqual(new, "ytdlp:jobs:failed:j1")
        self.assertEqual((h["error_code"], h["failed_count"]), ("login_required", "1"))
        self.assertEqual(self.c.profile_state(self.redis, "nico"), "expired")

    def test_record_failure_other_error_clears_code(self) -> None:
        key = self.make_failed("j2", auth_profile="nico", error_code="login_required")
        new = self.m.record_failure(key, "nico", "HTTP Error 500")
        self.assertEqual(self.redis.hgetall(new)["error_code"], "")
        self.assertEqual(self.c.profile_state(self.redis, "nico"), "valid")

    def test_retry_skips_jobs_waiting_for_login(self) -> None:
        # プロファイル未指定のログイン要求: 再試行しても解決しないため常に対象外
        self.redis.hset("ytdlp:jobs:failed:a", mapping={
            "failed_count": "1", "error_code": "login_required", "auth_profile": ""})
        self.assertIsNone(self.m.find_retryable_failed_key())
        # 失効中のプロファイル: cookie が入れ直されるまで対象外
        self.redis.hset("ytdlp:jobs:failed:b", mapping={
            "failed_count": "1", "error_code": "login_required", "auth_profile": "nico"})
        self.c.mark_expired(self.redis, "nico")
        self.assertIsNone(self.m.find_retryable_failed_key())
        os.utime(self.store, (time.time() + 10, time.time() + 10))
        self.assertEqual(self.m.find_retryable_failed_key(), "ytdlp:jobs:failed:b")

    def test_normal_failure_still_retryable(self) -> None:
        self.redis.hset("ytdlp:jobs:failed:c", mapping={"failed_count": "1", "error_code": ""})
        self.assertEqual(self.m.find_retryable_failed_key(), "ytdlp:jobs:failed:c")

    def test_job_hash_keeps_profile(self) -> None:
        key = self.m.make_job_hash("z", {"url": "u", "auth_profile": "nico"})
        self.assertEqual(self.redis.hgetall(key)["auth_profile"], "nico")


if __name__ == "__main__":
    unittest.main()
