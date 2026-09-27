"""dispatcher.py / updater.py / backends.py の単体テスト。"""
from __future__ import annotations

import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from helpers import FakeRedis, load_service

ROOT = Path(__file__).resolve().parent.parent
SRC = str(ROOT / "workerServer" / "src")


class UpdaterTest(unittest.TestCase):
    def setUp(self) -> None:
        for n in ("updater", "jobs", "dispatcher", "backends", "cookies"):
            sys.modules.pop(n, None)
        sys.path.insert(0, SRC)
        import updater
        self.updater = updater
        self.redis = FakeRedis()
        self.tmp = tempfile.mkdtemp()

    def tearDown(self) -> None:
        sys.path.remove(SRC)

    def test_asset_name_by_arch(self) -> None:
        with mock.patch("platform.machine", return_value="x86_64"):
            self.assertEqual(self.updater.asset_name(), "yt-dlp_musllinux")
        with mock.patch("platform.machine", return_value="aarch64"):
            self.assertEqual(self.updater.asset_name(), "yt-dlp_musllinux_aarch64")
        with mock.patch("platform.machine", return_value="mips"), \
                self.assertRaises(RuntimeError):
            self.updater.asset_name()

    def test_verify_checksum(self) -> None:
        p = Path(self.tmp) / "yt-dlp_musllinux"
        p.write_bytes(b"hello")
        digest = self.updater._sha256_of(p)
        sums = f"{digest}  yt-dlp_musllinux\nabc  other\n"
        self.assertTrue(
            self.updater._verify_checksum(p, sums, "yt-dlp_musllinux"))
        self.assertFalse(
            self.updater._verify_checksum(p, "deadbeef  yt-dlp_musllinux\n",
                                           "yt-dlp_musllinux"))

    def test_check_and_update_skips_within_interval(self) -> None:
        cfg = self.updater.UpdaterConfig(
            ytdlp_dir=Path(self.tmp), update_interval=21600)
        self.redis.hset(self.updater.UPDATER_HASH_KEY, mapping={
            "checked_at": str(time.time())})
        with mock.patch.object(self.updater, "latest_tag") as latest:
            self.updater.check_and_update(self.redis, cfg)
        latest.assert_not_called()

    def test_check_and_update_checks_when_due(self) -> None:
        cfg = self.updater.UpdaterConfig(
            ytdlp_dir=Path(self.tmp), update_interval=100)
        self.redis.hset(self.updater.UPDATER_HASH_KEY, mapping={
            "checked_at": str(time.time() - 1000)})
        with mock.patch.object(self.updater, "latest_tag", return_value="2099.01.01") as latest, \
                mock.patch.object(self.updater, "fetch_and_apply") as fetch, \
                mock.patch.object(self.updater, "_installed_version", return_value=None):
            self.updater.check_and_update(self.redis, cfg)
        latest.assert_called_once()
        fetch.assert_called_once_with(cfg, "2099.01.01")
        self.assertEqual(
            self.redis.hgetall(self.updater.UPDATER_HASH_KEY)["current"], "2099.01.01")

    def test_check_and_update_no_op_when_already_current(self) -> None:
        cfg = self.updater.UpdaterConfig(ytdlp_dir=Path(self.tmp))
        current_dir = Path(self.tmp) / "versions" / "2099.01.01"
        current_dir.mkdir(parents=True)
        (Path(self.tmp) / "current").symlink_to(Path("versions") / "2099.01.01")
        with mock.patch.object(self.updater, "latest_tag", return_value="2099.01.01"), \
                mock.patch.object(self.updater, "fetch_and_apply") as fetch, \
                mock.patch.object(
                    self.updater, "_installed_version", return_value="2099.01.01"):
            self.updater.check_and_update(self.redis, cfg)
        fetch.assert_not_called()

    def test_check_and_update_records_error_on_failure(self) -> None:
        cfg = self.updater.UpdaterConfig(ytdlp_dir=Path(self.tmp))
        with mock.patch.object(
                self.updater, "latest_tag", side_effect=OSError("network down")):
            self.updater.check_and_update(self.redis, cfg)
        h = self.redis.hgetall(self.updater.UPDATER_HASH_KEY)
        self.assertIn("network down", h["last_error"])

    def test_force_respects_cooldown(self) -> None:
        cfg = self.updater.UpdaterConfig(
            ytdlp_dir=Path(self.tmp), update_cooldown=1800)
        self.redis.hset(self.updater.UPDATER_HASH_KEY, mapping={
            "checked_at": str(time.time())})
        with mock.patch.object(self.updater, "latest_tag") as latest:
            self.updater.check_and_update(self.redis, cfg, force=True)
        latest.assert_not_called()


class BackendsTest(unittest.TestCase):
    def setUp(self) -> None:
        for n in ("backends", "jobs", "updater", "dispatcher", "cookies"):
            sys.modules.pop(n, None)
        sys.path.insert(0, SRC)
        import backends
        self.backends = backends

    def tearDown(self) -> None:
        sys.path.remove(SRC)

    def test_process_backend_tracks_running_and_exit_codes(self) -> None:
        proc = mock.Mock(pid=123)

        def _wait(timeout: float | None = None) -> int:  # noqa: ARG001
            time.sleep(0.2)  # running_count() を確認する猶予を作る
            return 0

        proc.wait.side_effect = _wait
        with mock.patch("subprocess.Popen", return_value=proc):
            backend = self.backends.ProcessBackend(cmd=["true"], env={})
            backend.start_worker()
            self.assertEqual(backend.running_count(), 1)
            # _wait_for はスレッドで動くため、完了を待つ
            self.assertTrue(backend.wake_event.wait(timeout=2))
        self.assertEqual(backend.running_count(), 0)
        self.assertEqual(backend.pop_recent_exit_codes(), [0])
        # 一度取り出したら空になる
        self.assertEqual(backend.pop_recent_exit_codes(), [])

    def test_process_backend_stop_all_terminates(self) -> None:
        proc = mock.Mock(pid=1)
        proc.wait.side_effect = [None]  # start_worker の _wait_for 用スレッドではブロックし続ける想定を回避
        with mock.patch("subprocess.Popen", return_value=proc):
            backend = self.backends.ProcessBackend(cmd=["sleep", "100"], env={})
            with mock.patch.object(backend, "_procs", {1: proc}):
                backend.stop_all(timeout=1)
        proc.terminate.assert_called_once()


class DispatcherTest(unittest.TestCase):
    def setUp(self) -> None:
        for n in ("dispatcher", "jobs", "updater", "backends", "cookies"):
            sys.modules.pop(n, None)
        sys.path.insert(0, SRC)
        import dispatcher
        self.dispatcher = dispatcher
        self.redis = FakeRedis()

    def tearDown(self) -> None:
        sys.path.remove(SRC)

    def make(self) -> object:
        cfg = self.dispatcher.Config()
        backend = mock.Mock()
        backend.wake_event = mock.Mock()
        backend.wake_event.is_set.return_value = False
        updater_cfg = mock.Mock()
        d = self.dispatcher.Dispatcher(cfg, backend, self.redis, updater_cfg)
        return d

    def test_worker_max_falls_back_to_worker_count(self) -> None:
        os.environ.pop("WORKER_MAX", None)
        os.environ["WORKER_COUNT"] = "4"
        try:
            cfg = self.dispatcher.Config()
            self.assertEqual(cfg.worker_max, 4)
        finally:
            os.environ.pop("WORKER_COUNT", None)

    def test_worker_max_takes_precedence(self) -> None:
        os.environ["WORKER_MAX"] = "2"
        os.environ["WORKER_COUNT"] = "9"
        try:
            cfg = self.dispatcher.Config()
            self.assertEqual(cfg.worker_max, 2)
        finally:
            os.environ.pop("WORKER_MAX", None)
            os.environ.pop("WORKER_COUNT", None)

    def test_backoff_increases_then_resets(self) -> None:
        d = self.make()
        self.assertEqual(d._backoff_seconds(), 0)
        d._consecutive_failures = 1
        self.assertEqual(d._backoff_seconds(), 5)
        d._consecutive_failures = 3
        self.assertEqual(d._backoff_seconds(), 20)
        d._consecutive_failures = 10
        self.assertEqual(d._backoff_seconds(), 60)  # 上限で頭打ち

    def test_consume_exit_codes_updates_failure_streak(self) -> None:
        d = self.make()
        d.backend.pop_recent_exit_codes.return_value = [1, 1]
        d._consume_exit_codes()
        self.assertEqual(d._consecutive_failures, 2)
        d.backend.pop_recent_exit_codes.return_value = [0]
        d._consume_exit_codes()
        self.assertEqual(d._consecutive_failures, 0)

    def test_start_one_starts_backend_worker(self) -> None:
        d = self.make()
        d._start_one()
        d.backend.start_worker.assert_called_once()


if __name__ == "__main__":
    unittest.main()
