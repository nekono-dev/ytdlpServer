import json
import os
import subprocess
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from helpers import FakeRedis, load_service

LOGIN_ERR = "ERROR: Sensitive content, login required. Use --cookies, --cookies-from-browser ..."


def make_proc(returncode: int = 0, stdout: str = "ok", stderr: str = "",
              communicate_side_effect: object = None) -> mock.Mock:
    """subprocess.Popen の戻り値を模したモック (function.run_yt_dlp 用)。"""
    proc = mock.Mock()
    proc.returncode = returncode
    if communicate_side_effect is not None:
        def _communicate() -> tuple[str, str]:
            communicate_side_effect()
            return stdout, stderr
        proc.communicate.side_effect = _communicate
    else:
        proc.communicate.return_value = (stdout, stderr)
    proc.poll.return_value = returncode
    return proc


class FunctionTest(unittest.TestCase):
    """function.run_yt_dlp (Popen ベース) の単体テスト。"""

    def setUp(self) -> None:
        self.dir = tempfile.mkdtemp()
        self.redis = FakeRedis()
        self.m = load_service("workerServer", self.dir, self.redis)
        self.c = self.m.cookies
        self.store = Path(self.dir) / "nico.txt"
        self.store.write_bytes(b"# Netscape\nTOPSECRETVALUE\n")
        os.environ["DOWNLOAD_DIR"] = self.dir

    def run_job(self, proc: mock.Mock, **job: object) -> tuple[tuple, mock.Mock]:
        import function
        function.SAVEDIR = Path(self.dir)
        with mock.patch("subprocess.Popen", return_value=proc) as popen:
            result = function.run_yt_dlp({
                "url": "https://x/1", "options": [], "filename": "f", "id": "i",
                **job})
        return result, popen

    def test_cmd_has_cookies_and_log_is_masked(self) -> None:
        with mock.patch("builtins.print") as pr:
            (ok, _), popen = self.run_job(make_proc(stdout="ok"), auth_profile="nico")
        self.assertTrue(ok)
        cmd = popen.call_args.args[0]
        tmp = cmd[cmd.index("--cookies") + 1]
        self.assertNotEqual(tmp, str(self.store))
        logged = " ".join(str(a) for c in pr.call_args_list for a in c.args)
        self.assertNotIn(tmp, logged)
        self.assertIn("<cookies>", logged)

    def test_no_profile_no_cookies_flag(self) -> None:
        _, popen = self.run_job(make_proc(stdout="ok"))
        self.assertNotIn("--cookies", popen.call_args.args[0])

    def test_missing_profile_fails_as_login_required(self) -> None:
        (ok, out), _ = self.run_job(make_proc(), auth_profile="ghost")
        self.assertFalse(ok)
        self.assertTrue(self.c.classify_login_required(out))

    def test_failure_still_writes_back_cookies(self) -> None:
        captured: dict[str, list] = {}

        def fail() -> None:
            cmd = captured["cmd"]
            Path(cmd[cmd.index("--cookies") + 1]).write_bytes(b"REFRESHED")

        proc = make_proc(returncode=1, stdout="", stderr="boom",
                          communicate_side_effect=fail)

        def popen_factory(cmd: list, **_kw: object) -> mock.Mock:
            captured["cmd"] = cmd
            return proc

        import function
        function.SAVEDIR = Path(self.dir)
        with mock.patch("subprocess.Popen", side_effect=popen_factory):
            ok, out = function.run_yt_dlp({
                "url": "https://x/1", "options": [], "filename": "f", "id": "i",
                "auth_profile": "nico"})
        self.assertEqual((ok, out), (False, "boom"))
        self.assertEqual(self.store.read_bytes(), b"REFRESHED")

    def test_on_process_start_receives_popen(self) -> None:
        proc = make_proc(stdout="ok")
        holder: dict[str, object] = {}
        import function
        function.SAVEDIR = Path(self.dir)
        with mock.patch("subprocess.Popen", return_value=proc):
            function.run_yt_dlp(
                {"url": "https://x/1", "options": [], "filename": "f", "id": "i"},
                on_process_start=lambda p: holder.setdefault("proc", p))
        self.assertIs(holder["proc"], proc)


class JobsTest(unittest.TestCase):
    """jobs.py (状態遷移・取得・回収) の単体テスト。"""

    def setUp(self) -> None:
        self.dir = tempfile.mkdtemp()
        self.redis = FakeRedis()
        self.m = load_service("workerServer", self.dir, self.redis)
        self.jobs = self.m.jobs

    def test_take_from_queue_then_commit(self) -> None:
        self.redis.rpush("ytdlp:queue", '{"id":"j1","url":"u"}')
        raw = self.jobs.take_from_queue(self.redis, "w1")
        self.assertEqual(raw, '{"id":"j1","url":"u"}')
        self.assertEqual(self.redis.lrange("ytdlp:processing:w1", 0, -1), [raw])
        key = self.jobs.commit_from_processing(
            self.redis, "w1", "j1", json.loads(raw), ttl=100)
        self.assertEqual(key, "ytdlp:jobs:in_progress:j1")
        self.assertEqual(self.redis.hgetall(key)["worker_id"], "w1")
        self.assertEqual(self.redis.lrange("ytdlp:processing:w1", 0, -1), [])

    def test_take_from_queue_empty_returns_none(self) -> None:
        self.assertIsNone(self.jobs.take_from_queue(self.redis, "w1"))

    def test_discard_processing_moves_to_dead_queue(self) -> None:
        self.jobs.discard_processing(self.redis, "w1", "not json")
        self.assertEqual(self.redis.lrange("ytdlp:queue:dead", 0, -1), ["not json"])
        self.assertEqual(self.redis.llen("ytdlp:processing:w1"), 0)

    def test_take_retryable_failed_claims_ownership_once(self) -> None:
        self.redis.hset("ytdlp:jobs:failed:a", mapping={"failed_count": "1"})
        key = self.jobs.take_retryable_failed(self.redis, retry_count=5, worker_id="w1")
        self.assertEqual(key, "ytdlp:jobs:in_progress:a")
        self.assertEqual(self.redis.hgetall(key)["worker_id"], "w1")
        self.assertIsNone(
            self.jobs.take_retryable_failed(self.redis, retry_count=5, worker_id="w2"))

    def test_take_retryable_failed_skips_exhausted(self) -> None:
        self.redis.hset("ytdlp:jobs:failed:a", mapping={"failed_count": "5"})
        self.assertIsNone(
            self.jobs.take_retryable_failed(self.redis, retry_count=5, worker_id="w1"))

    def test_take_retryable_failed_skips_login_wait(self) -> None:
        self.redis.hset("ytdlp:jobs:failed:a", mapping={
            "failed_count": "1", "error_code": "login_required", "auth_profile": ""})
        self.assertIsNone(
            self.jobs.take_retryable_failed(self.redis, retry_count=5, worker_id="w1"))

    def test_count_pending_targets(self) -> None:
        self.redis.rpush("ytdlp:queue", "a", "b")
        self.redis.hset("ytdlp:jobs:failed:x", mapping={"failed_count": "0"})
        self.assertEqual(
            self.jobs.count_pending_targets(self.redis, retry_count=5, cap=10), 3)

    def test_count_pending_targets_capped(self) -> None:
        self.redis.rpush("ytdlp:queue", "a", "b", "c")
        self.assertEqual(
            self.jobs.count_pending_targets(self.redis, retry_count=5, cap=2), 3)

    def test_lease_lifecycle(self) -> None:
        self.jobs.acquire_lease(self.redis, "w1", ttl=100)
        self.assertTrue(self.jobs.lease_alive(self.redis, "w1"))
        self.jobs.release_lease(self.redis, "w1")
        self.assertFalse(self.jobs.lease_alive(self.redis, "w1"))

    def test_mark_interrupted_no_consume(self) -> None:
        self.redis.hset("ytdlp:jobs:in_progress:k", mapping={
            "failed_count": "0", "worker_id": "w1"})
        new_key = self.jobs.mark_interrupted(
            self.redis, "ytdlp:jobs:in_progress:k", consume_retry=False,
            reason="stopped", ttl=100)
        h = self.redis.hgetall(new_key)
        self.assertEqual(new_key, "ytdlp:jobs:failed:k")
        self.assertEqual(h["failed_count"], "0")
        self.assertEqual(h["error_code"], "interrupted")

    def test_mark_interrupted_consumes_retry(self) -> None:
        self.redis.hset("ytdlp:jobs:in_progress:k", mapping={
            "failed_count": "0", "worker_id": "w1"})
        new_key = self.jobs.mark_interrupted(
            self.redis, "ytdlp:jobs:in_progress:k", consume_retry=True,
            reason="crashed", ttl=100)
        self.assertEqual(self.redis.hgetall(new_key)["failed_count"], "1")

    def test_is_owner(self) -> None:
        self.redis.hset("k", mapping={"worker_id": "w1"})
        self.assertTrue(self.jobs.is_owner(self.redis, "k", "w1"))
        self.assertFalse(self.jobs.is_owner(self.redis, "k", "w2"))

    def test_reclaim_stale_in_progress_without_lease(self) -> None:
        self.redis.hset("ytdlp:jobs:in_progress:k", mapping={
            "worker_id": "gone", "failed_count": "0",
            "started_at": str(time.time())})
        n = self.jobs.reclaim_stale(self.redis, inprogress_stale=21600, ttl=100)
        self.assertEqual(n, 1)
        h = self.redis.hgetall("ytdlp:jobs:failed:k")
        self.assertEqual(h["error_code"], "interrupted")
        self.assertEqual(h["failed_count"], "1")

    def test_reclaim_stale_keeps_in_progress_with_live_lease(self) -> None:
        self.jobs.acquire_lease(self.redis, "alive", ttl=100)
        self.redis.hset("ytdlp:jobs:in_progress:k2", mapping={
            "worker_id": "alive", "failed_count": "0",
            "started_at": str(time.time())})
        n = self.jobs.reclaim_stale(self.redis, inprogress_stale=21600, ttl=100)
        self.assertEqual(n, 0)
        self.assertIn("ytdlp:jobs:in_progress:k2", self.redis.hashes)

    def test_reclaim_stale_legacy_in_progress_without_worker_id(self) -> None:
        # 導入前 (worker_id 無し) の in_progress は、経過時間で回収する
        self.redis.hset("ytdlp:jobs:in_progress:legacy", mapping={
            "failed_count": "0", "started_at": str(time.time() - 100000)})
        n = self.jobs.reclaim_stale(self.redis, inprogress_stale=21600, ttl=100)
        self.assertEqual(n, 1)

    def test_reclaim_stale_legacy_in_progress_not_yet_stale(self) -> None:
        self.redis.hset("ytdlp:jobs:in_progress:legacy2", mapping={
            "failed_count": "0", "started_at": str(time.time())})
        n = self.jobs.reclaim_stale(self.redis, inprogress_stale=21600, ttl=100)
        self.assertEqual(n, 0)

    def test_reclaim_stale_processing_returns_to_queue_head(self) -> None:
        self.redis.rpush("ytdlp:queue", "existing")
        self.redis.rpush("ytdlp:processing:dead", '{"id":"z","url":"u"}')
        n = self.jobs.reclaim_stale(self.redis, inprogress_stale=21600, ttl=100)
        self.assertEqual(n, 1)
        self.assertEqual(
            self.redis.lrange("ytdlp:queue", 0, -1),
            ['{"id":"z","url":"u"}', "existing"])

    def test_reclaim_stale_processing_duplicate_not_requeued(self) -> None:
        # commit 済みで processing の削除だけが取り残されたケース: 二重に戻さない
        self.redis.hset("ytdlp:jobs:in_progress:z2", mapping={
            "worker_id": "w", "failed_count": "0"})
        self.redis.rpush("ytdlp:processing:dead2", '{"id":"z2","url":"u"}')
        n = self.jobs.reclaim_stale(self.redis, inprogress_stale=21600, ttl=100)
        # processing の重複は数えないが、in_progress:z2 自体は (worker_id "w" の
        # リースが無いため) 別途 1 件回収される
        self.assertEqual(n, 1)
        self.assertEqual(self.redis.llen("ytdlp:queue"), 0)
        self.assertEqual(
            self.redis.hgetall("ytdlp:jobs:failed:z2")["error_code"], "interrupted")


class MainRecordFailureTest(unittest.TestCase):
    """main.py の cookie 連携部分 (record_failure) の単体テスト。"""

    def setUp(self) -> None:
        self.dir = tempfile.mkdtemp()
        self.redis = FakeRedis()
        self.m = load_service("workerServer", self.dir, self.redis)
        self.c = self.m.cookies
        self.store = Path(self.dir) / "nico.txt"
        self.store.write_bytes(b"# Netscape\nTOPSECRETVALUE\n")

    def make_in_progress(self, jid: str, **fields: str) -> str:
        key = f"ytdlp:jobs:in_progress:{jid}"
        self.redis.hset(key, mapping={"failed_count": "0", **fields})
        return key

    def test_record_failure_login_required(self) -> None:
        key = self.make_in_progress("j1", auth_profile="nico")
        new = self.m.record_failure(key, "nico", LOGIN_ERR)
        h = self.redis.hgetall(new)
        self.assertEqual(new, "ytdlp:jobs:failed:j1")
        self.assertEqual((h["error_code"], h["failed_count"]), ("login_required", "1"))
        self.assertEqual(self.c.profile_state(self.redis, "nico"), "expired")

    def test_record_failure_login_url(self) -> None:
        from urllib.parse import parse_qs, urlsplit
        self.c.BROWSER_UI_URL = "http://h:8080"
        key = self.make_in_progress("j3", auth_profile="nico")
        new = self.m.record_failure(
            key, "nico", LOGIN_ERR, "https://www.nicovideo.jp/watch/sm1")
        q = parse_qs(urlsplit(self.redis.hgetall(new)["login_url"]).query)
        self.assertEqual(
            q, {"profile": ["nico"], "start_url": ["https://www.nicovideo.jp/"]})

    def test_record_failure_login_url_resolves_profile(self) -> None:
        from urllib.parse import parse_qs, urlsplit
        self.c.BROWSER_UI_URL = "http://h:8080"
        key = self.make_in_progress("j4")
        new = self.m.record_failure(key, None, LOGIN_ERR, "https://x.com/a/status/1")
        q = parse_qs(urlsplit(self.redis.hgetall(new)["login_url"]).query)
        self.assertEqual(q["profile"], ["x"])

    def test_record_failure_other_error_clears_code(self) -> None:
        key = self.make_in_progress(
            "j2", auth_profile="nico", error_code="login_required")
        new = self.m.record_failure(key, "nico", "HTTP Error 500")
        self.assertEqual(self.redis.hgetall(new)["error_code"], "")
        self.assertEqual(self.c.profile_state(self.redis, "nico"), "valid")


class RunOnceTest(unittest.TestCase):
    """main.run_once (取得 → 実行 → 記録) の一連の流れ。"""

    def setUp(self) -> None:
        self.dir = tempfile.mkdtemp()
        self.redis = FakeRedis()
        self.m = load_service("workerServer", self.dir, self.redis)
        os.environ["DOWNLOAD_DIR"] = self.dir

    def run_once_with(self, ok: bool, output: str = "out") -> object:
        stop = self.m.threading.Event()
        with mock.patch.object(self.m, "run_yt_dlp", return_value=(ok, output)):
            return self.m.run_once("w1", stop)

    def test_no_job_returns_immediately(self) -> None:
        stop = self.m.threading.Event()
        rc = self.m.run_once("w1", stop)
        self.assertEqual(rc, 0)

    def test_completed_job_from_queue(self) -> None:
        self.redis.rpush(
            "ytdlp:queue",
            json.dumps({"id": "j1", "url": "u", "options": [], "filename": "f"}))
        self.run_once_with(True, "done")
        h = self.redis.hgetall("ytdlp:jobs:completed:j1")
        self.assertEqual(h["status"], "completed")
        self.assertEqual(h["output"], "done")

    def test_failed_job_increments_failed_count(self) -> None:
        self.redis.rpush(
            "ytdlp:queue",
            json.dumps({"id": "j2", "url": "u", "options": [], "filename": "f"}))
        self.run_once_with(False, "boom")
        h = self.redis.hgetall("ytdlp:jobs:failed:j2")
        self.assertEqual(h["failed_count"], "1")

    def test_stop_before_execution_marks_interrupted_without_consuming(self) -> None:
        self.redis.rpush(
            "ytdlp:queue",
            json.dumps({"id": "j3", "url": "u", "options": [], "filename": "f"}))
        stop = self.m.threading.Event()
        stop.set()
        self.m.run_once("w1", stop)
        h = self.redis.hgetall("ytdlp:jobs:failed:j3")
        self.assertEqual(h["error_code"], "interrupted")
        self.assertEqual(h["failed_count"], "0")

    def test_lost_ownership_does_not_record_result(self) -> None:
        self.redis.rpush(
            "ytdlp:queue",
            json.dumps({"id": "j4", "url": "u", "options": [], "filename": "f"}))

        def fake_run(job: dict, on_process_start: object = None) -> tuple[bool, str]:
            # 実行中に、dispatcher が回収して別 worker が所有した状況を再現する
            self.redis.hset("ytdlp:jobs:in_progress:j4", mapping={"worker_id": "other"})
            return True, "done"

        with mock.patch.object(self.m, "run_yt_dlp", side_effect=fake_run):
            self.m.run_once("w1", self.m.threading.Event())
        # 記録が書き換わっていない (completed になっていない)
        self.assertEqual(self.redis.hgetall("ytdlp:jobs:in_progress:j4")["worker_id"],
                          "other")
        self.assertNotIn("ytdlp:jobs:completed:j4", self.redis.hashes)

    def test_dead_letter_on_invalid_json(self) -> None:
        self.redis.rpush("ytdlp:queue", "not-json")
        rc = self.m.run_once("w1", self.m.threading.Event())
        self.assertEqual(rc, 0)
        self.assertEqual(self.redis.lrange("ytdlp:queue:dead", 0, -1), ["not-json"])

    def test_retries_failed_job_before_queue(self) -> None:
        self.redis.hset("ytdlp:jobs:failed:r1", mapping={
            "failed_count": "1", "url": "u", "options": "[]", "filename": "f"})
        self.redis.rpush(
            "ytdlp:queue",
            json.dumps({"id": "should-not-run", "url": "u", "options": []}))
        self.run_once_with(True, "done")
        self.assertIn("ytdlp:jobs:completed:r1", self.redis.hashes)
        # キューのジョブは手つかずのまま
        self.assertEqual(self.redis.llen("ytdlp:queue"), 1)


if __name__ == "__main__":
    unittest.main()
