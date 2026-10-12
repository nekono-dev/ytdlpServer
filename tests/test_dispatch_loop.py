"""dispatcher の監視ループ・通知・Docker バックエンドの単体テスト (dispatcher 仕様の受け入れ条件)。"""
from __future__ import annotations

import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from helpers import FakeRedis

ROOT = Path(__file__).resolve().parent.parent
SRC = str(ROOT / "workerServer" / "src")
NAMES = ("dispatcher", "jobs", "updater", "backends", "cookies", "docker")


class LoopTest(unittest.TestCase):
    def setUp(self) -> None:
        self.cookie_dir = tempfile.mkdtemp()
        os.environ["COOKIE_DIR"] = self.cookie_dir
        for n in NAMES:
            sys.modules.pop(n, None)
        sys.path.insert(0, SRC)
        import dispatcher
        import jobs
        self.dispatcher, self.jobs = dispatcher, jobs
        self.redis = FakeRedis()
        self.backend = mock.Mock()
        self.backend.running_count.return_value = 0
        self.backend.pop_recent_exit_codes.return_value = []
        self.d = dispatcher.Dispatcher(dispatcher.Config(), self.backend, self.redis, mock.Mock())

    def tearDown(self) -> None:
        sys.path.remove(SRC)

    def run_one_cycle(self) -> None:
        """監視ループを1周だけ回す (待機の代わりに停止のフラグを立てる)。"""
        def stop_after_wait(timeout: float | None = None) -> bool:  # noqa: ARG001
            self.d.stopping.set()
            return True

        with mock.patch.object(self.d.wake, "wait", side_effect=stop_after_wait):
            self.d._main_loop()

    def test_starts_workers_only_when_jobs_exist(self) -> None:
        self.run_one_cycle()
        self.backend.start_worker.assert_not_called()
        self.d.stopping.clear()
        self.redis.rpush("ytdlp:queue", "a")
        self.run_one_cycle()
        self.assertEqual(self.backend.start_worker.call_count, 1)

    def test_login_waiting_jobs_are_not_counted_until_cookie_is_refreshed(self) -> None:
        self.redis.hset("ytdlp:jobs:failed:a", mapping={
            "failed_count": "1", "error_code": "login_required", "auth_profile": ""})
        self.redis.hset("ytdlp:jobs:failed:b", mapping={
            "failed_count": "1", "error_code": "login_required", "auth_profile": "nico"})
        cookie = Path(self.cookie_dir) / "nico.txt"
        cookie.write_text("x")
        os.utime(cookie, (1000, 1000))
        self.jobs.cookies.mark_expired(self.redis, "nico")
        self.assertEqual(self.jobs.count_pending_targets(self.redis, 5, cap=10), 0)
        os.utime(cookie, (time.time() + 10, time.time() + 10))     # cookie を置き直す
        self.assertEqual(self.jobs.count_pending_targets(self.redis, 5, cap=10), 1)

    def test_scan_detects_jobs_without_notification(self) -> None:
        self.redis.rpush("ytdlp:queue", "a")
        self.assertFalse(self.d.wake.is_set())
        self.run_one_cycle()
        self.assertEqual(self.backend.start_worker.call_count, 1)

    def test_pubsub_wakes_loop_and_flags_update_check(self) -> None:
        d = self.d

        class PubSub:
            def subscribe(self, channel: str) -> None:
                assert channel == "ytdlp:events"

            def listen(self):
                yield {"type": "subscribe", "data": 1}
                yield {"type": "message", "data": "queued"}
                assert d.wake.is_set() and not d._force_update.is_set()
                d.wake.clear()
                yield {"type": "message", "data": "check_update"}
                d.stopping.set()
                yield {"type": "message", "data": "queued"}

        self.redis.pubsub = lambda: PubSub()
        d._pubsub_loop()
        self.assertTrue(d._force_update.is_set())

    def test_start_count_is_min_of_free_and_pending(self) -> None:
        self.redis.rpush("ytdlp:queue", "a", "b", "c")
        self.backend.running_count.return_value = 1                # 最大数 2 のうち 1 台が実行中
        self.d.cfg.worker_max = 2
        self.run_one_cycle()
        self.assertEqual(self.backend.start_worker.call_count, 1)
        self.backend.start_worker.reset_mock()
        self.d.stopping.clear()
        self.backend.running_count.return_value = 2                # 空きが 0
        self.run_one_cycle()
        self.backend.start_worker.assert_not_called()

    def test_loop_reclaims_jobs_of_workers_without_lease(self) -> None:
        self.redis.hset("ytdlp:jobs:in_progress:j", mapping={
            "worker_id": "dead", "failed_count": "0", "status": "in_progress"})
        self.run_one_cycle()
        failed = self.redis.hgetall("ytdlp:jobs:failed:j")
        self.assertEqual((failed["error_code"], failed["failed_count"]), ("interrupted", "1"))

    def test_run_stops_started_workers_on_shutdown(self) -> None:
        with mock.patch.object(self.d, "_main_loop"), \
                mock.patch("threading.Thread"), mock.patch("signal.signal"):
            self.d.run()
        self.backend.stop_all.assert_called_once_with(timeout=self.d.cfg.stop_timeout)

    def test_config_defaults(self) -> None:
        with mock.patch.dict(os.environ, {}, clear=True):
            cfg = self.dispatcher.Config()
        self.assertEqual((cfg.scan_interval, cfg.lease_ttl, cfg.worker_max, cfg.mode),
                         (30, 60, 1, "process"))

    def test_backoff_constants(self) -> None:
        self.assertEqual((self.dispatcher.BACKOFF_BASE, self.dispatcher.BACKOFF_MAX), (5, 60))

    def test_dispatcher_does_not_read_job_contents(self) -> None:
        for name in ("dispatcher.py", "backends.py"):
            text = (ROOT / "workerServer/src" / name).read_text(encoding="utf-8")
            self.assertNotIn("json.loads", text, name)
            self.assertNotIn("import json", text, name)


class DockerBackendTest(unittest.TestCase):
    def setUp(self) -> None:
        for n in NAMES:
            sys.modules.pop(n, None)
        self.docker = mock.MagicMock()
        sys.modules["docker"] = self.docker
        sys.path.insert(0, SRC)
        import backends
        self.backends = backends
        self.client = self.docker.DockerClient.return_value
        self.client.events.return_value = iter(())
        self.client.containers.get.return_value.attrs = {
            "Image": "sha256:img", "Config": {"Env": ["REDIS_URL=redis://redis", "WORKER_MAX=2"]}}
        with mock.patch("threading.Thread"):
            self.backend = backends.DockerBackend(
                "tcp://proxy:2375", "me", ["python3", "-u", "/workspace/main.py"], 30)

    def tearDown(self) -> None:
        sys.path.remove(SRC)
        sys.modules.pop("docker", None)

    def test_worker_container_shares_image_mounts_and_network_with_fixed_command(self) -> None:
        self.backend.start_worker()
        kwargs = self.client.containers.run.call_args.kwargs
        self.assertEqual(kwargs["image"], "sha256:img")
        self.assertEqual(kwargs["command"], ["python3", "-u", "/workspace/main.py"])
        self.assertEqual(kwargs["volumes_from"], ["me"])
        self.assertEqual(kwargs["network_mode"], "container:me")
        self.assertEqual(kwargs["labels"], {"ytdlp.role": "worker"})
        self.assertEqual(kwargs["environment"], ["REDIS_URL=redis://redis", "WORKER_MAX=2"])
        self.assertTrue(kwargs["auto_remove"])

    def test_running_workers_are_counted_by_label(self) -> None:
        self.client.containers.list.return_value = [mock.Mock(), mock.Mock()]
        self.assertEqual(self.backend.reconcile(), 2)
        self.assertEqual(self.backend.running_count(), 2)
        self.client.containers.list.assert_called_with(filters={"label": ["ytdlp.role=worker"]})

    def test_stop_all_stops_every_worker(self) -> None:
        workers = [mock.Mock(), mock.Mock()]
        self.client.containers.list.return_value = workers
        self.backend.stop_all(timeout=30)
        for w in workers:
            w.stop.assert_called_once_with(timeout=30)

    def test_exit_events_record_exit_codes_and_wake(self) -> None:
        self.client.events.return_value = iter([
            {"Actor": {"Attributes": {"exitCode": "0"}}},
            {"Actor": {"Attributes": {"exitCode": "137"}}}])
        self.backend._watch_events()
        self.assertEqual(self.backend.pop_recent_exit_codes(), [0, 137])
        self.assertTrue(self.backend.wake_event.is_set())


if __name__ == "__main__":
    unittest.main()
