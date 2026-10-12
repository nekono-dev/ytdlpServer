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

    def reserve(self, base: str, job_id: str) -> str:
        return self.m.jobs.reserve_output_name(self.redis, Path(self.download), base, job_id, 100)

    def test_output_name_gets_numbers_when_destination_has_same_name(self) -> None:
        self.assertEqual(self.reserve("t", "j0"), "t")
        (Path(self.download) / "u.mp4").write_text("x")
        self.assertEqual(self.reserve("u", "j1"), "u (1)")
        (Path(self.download) / "u (1).mp4.part").write_text("x")
        self.assertEqual(self.reserve("u", "j2"), "u (2)")

    def test_output_name_treats_other_extensions_and_partial_files_as_same_name(self) -> None:
        (Path(self.download) / "v.f137.mp4.part").write_text("x")
        self.assertEqual(self.reserve("v", "j1"), "v (1)")
        (Path(self.download) / "w (1).mp4").write_text("x")
        self.assertEqual(self.reserve("w", "j2"), "w", "連番つきの別名は w の競合にならない")

    def test_output_name_is_recorded_stable_and_not_shared_between_jobs(self) -> None:
        self.assertEqual(self.reserve("x", "ja"), "x")
        self.assertEqual(self.reserve("x", "ja"), "x", "同じジョブなら同じ名前")
        self.assertEqual(self.reserve("x", "jb"), "x (1)", "同時に走る別のジョブとは重ならない")
        self.redis.rpush("ytdlp:queue", json.dumps({"id": "jc", "url": "u", "options": [], "filename": "y"}))
        (Path(self.download) / "y.mp4").write_text("old")
        jobs_seen: list[dict] = []

        def fake_run(job: dict, on_process_start: object = None) -> tuple[bool, str]:
            jobs_seen.append(dict(job))
            return False, "boom"

        with mock.patch.object(self.m, "run_yt_dlp", side_effect=fake_run):
            self.m.run_once("w1", threading.Event())
        failed = self.redis.hgetall("ytdlp:jobs:failed:jc")
        self.assertEqual((jobs_seen[0]["output_name"], failed["output_name"]), ("y (1)", "y (1)"))
        with mock.patch.object(self.m, "run_yt_dlp", side_effect=fake_run):
            self.m.run_once("w1", threading.Event())
        self.assertEqual(jobs_seen[1]["output_name"], "y (1)", "再実行では記録された名前を使う")

    def test_worker_id_comes_from_environment(self) -> None:
        with mock.patch.dict(os.environ, {"WORKER_ID": "w-fixed"}), \
                mock.patch.object(self.m.jobs, "acquire_lease") as acquire, \
                mock.patch.object(self.m, "run_once", return_value=0), \
                mock.patch.object(self.m.signal, "signal"):
            self.m.main()
        self.assertEqual(acquire.call_args.args[1], "w-fixed")

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
