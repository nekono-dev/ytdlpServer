"""ワーカーの停止・リースの失効・出力先の単体テスト (worker-runtime 仕様の受け入れ条件)。"""
from __future__ import annotations

import json
import os
import signal
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

from helpers import FakeRedis, load_service


class RuntimeTest(unittest.TestCase):
    def setUp(self) -> None:
        self.dir = tempfile.mkdtemp()
        self.download = tempfile.mkdtemp()
        os.environ["DOWNLOAD_DIR"] = self.download
        self.redis = FakeRedis()
        self.m = load_service("workerServer", self.dir, self.redis)
        self.function = __import__("function")

    def test_stop_request_terminates_ytdlp_and_kills_after_grace(self) -> None:
        stop = threading.Event()
        proc = mock.Mock()
        proc.poll.return_value = None
        proc.wait.side_effect = [__import__("subprocess").TimeoutExpired("yt-dlp", 1), None]
        release = threading.Event()

        def fake_run(job: dict, on_process_start: object = None) -> tuple[bool, str]:
            on_process_start(proc)
            release.wait(5)
            return False, "killed"

        def set_stop() -> None:
            time.sleep(0.5)
            stop.set()
            release.set()

        threading.Thread(target=set_stop, daemon=True).start()
        with mock.patch.object(self.m, "run_yt_dlp", side_effect=fake_run):
            status, _ = self.m._execute({"id": "j"}, stop)
        self.assertEqual(status, "interrupted")
        proc.send_signal.assert_called_once_with(signal.SIGTERM)
        proc.kill.assert_called_once()

    def test_heartbeat_failure_for_lease_ttl_stops_the_job(self) -> None:
        stop, finished = threading.Event(), threading.Event()
        with mock.patch.object(self.m, "LEASE_TTL", 0), \
                mock.patch.object(self.m, "HEARTBEAT_INTERVAL", 0.01), \
                mock.patch.object(self.m.jobs, "renew_lease", side_effect=OSError("redis down")):
            t = threading.Thread(target=self.m._heartbeat_loop, args=("w1", finished, stop))
            t.start()
            self.assertTrue(stop.wait(2))
            finished.set()
            t.join(2)

    def test_output_template_uses_savedir_filename_or_job_id(self) -> None:
        seen: list[list[str]] = []

        class Proc:
            returncode = 0

            def __init__(self, cmd: list[str], **kw: object) -> None:
                seen.append(cmd)

            def communicate(self) -> tuple[str, str]:
                return "ok", ""

        with mock.patch("subprocess.Popen", Proc):
            self.function.run_yt_dlp({"id": "j1", "url": "u", "savedir": "dir", "filename": "a/b:c"})
            self.function.run_yt_dlp({"id": "j2", "url": "u", "savedir": "", "filename": ""})
            self.function.run_yt_dlp({"id": "j1", "url": "u", "savedir": "dir", "filename": "a/b:c"})
        outs = [c[c.index("-o") + 1] for c in seen]
        self.assertEqual(outs[0], str(Path(self.download) / "dir" / "b_c.%(ext)s"))
        self.assertEqual(outs[1], str(Path(self.download) / "j2.%(ext)s"))
        self.assertEqual(outs[0], outs[2], "再実行でも同じ出力先を使う")
        self.assertNotIn("--no-continue", seen[0])
        self.assertTrue((Path(self.download) / "dir").is_dir())

    def test_failed_job_is_retried_with_same_destination(self) -> None:
        self.redis.hset("ytdlp:jobs:failed:r1", mapping={
            "failed_count": "1", "url": "u", "options": "[]", "savedir": "dir", "filename": "f"})
        jobs_seen: list[dict] = []

        def fake_run(job: dict, on_process_start: object = None) -> tuple[bool, str]:
            jobs_seen.append(job)
            return True, "ok"

        with mock.patch.object(self.m, "run_yt_dlp", side_effect=fake_run):
            self.m.run_once("w1", threading.Event())
        self.assertEqual((jobs_seen[0]["savedir"], jobs_seen[0]["filename"]), ("dir", "f"))

    def test_youtube_defaults_added_and_explicit_choice_kept(self) -> None:
        added = self.function.with_youtube_defaults(["-f", "b"])
        self.assertEqual(added[-2:], ["--extractor-args", "youtube:player_client=default,mweb"])
        explicit = ["--extractor-args", "youtube:player_client=ios"]
        self.assertEqual(self.function.with_youtube_defaults(explicit), explicit)
        merged = self.function.with_youtube_defaults(["--extractor-args", "youtube:lang=ja"])
        self.assertEqual(merged, ["--extractor-args", "youtube:lang=ja;player_client=default,mweb"])

    def test_default_settings(self) -> None:
        self.assertEqual((self.m.STOP_GRACE, self.m.HEARTBEAT_INTERVAL, self.m.LEASE_TTL), (20, 10, 60))

    def test_failure_record_never_contains_cookie_values(self) -> None:
        self.redis.hset("ytdlp:jobs:in_progress:s", mapping={"failed_count": "0"})
        cookie = Path(self.dir) / "nico.txt"
        cookie.write_text("# Netscape\nTOPSECRETVALUE\n")
        job = {"id": "s", "url": "https://www.nicovideo.jp/x", "auth_profile": "nico"}
        # yt-dlp の出力に cookie の値は含まれない。ジョブの記録にも値は入らない。
        self.m.record_failure("ytdlp:jobs:in_progress:s", job["auth_profile"], "login required", job["url"])
        self.assertNotIn("TOPSECRETVALUE", json.dumps(self.redis.hashes))


if __name__ == "__main__":
    unittest.main()
