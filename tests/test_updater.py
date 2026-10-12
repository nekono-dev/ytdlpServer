"""updater.py の更新処理の単体テスト (ytdlp-updater 仕様の受け入れ条件)。"""
from __future__ import annotations

import os
import re
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from helpers import FakeRedis

ROOT = Path(__file__).resolve().parent.parent
SRC = str(ROOT / "workerServer" / "src")
NEW = "2099.01.01"


class UpdateTest(unittest.TestCase):
    def setUp(self) -> None:
        for n in ("updater", "jobs", "dispatcher", "backends", "cookies"):
            sys.modules.pop(n, None)
        sys.path.insert(0, SRC)
        import updater
        self.updater = updater
        self.redis = FakeRedis()
        self.tmp = Path(tempfile.mkdtemp())
        self.cfg = updater.UpdaterConfig(ytdlp_dir=self.tmp, keep_versions=2)

    def tearDown(self) -> None:
        sys.path.remove(SRC)

    def fake_download(self, sums_digest: str | None = None):
        body = b"BINARY"

        def download(url: str, dest: Path) -> None:
            if url.endswith("SHA2-256SUMS"):
                digest = sums_digest or self.updater.hashlib.sha256(body).hexdigest()
                dest.write_text(f"{digest}  yt-dlp_linux\n", encoding="utf-8")
            else:
                dest.write_bytes(body)

        return download

    def apply(self, *, installed: str | None = NEW, digest: str | None = None) -> None:
        with mock.patch.object(self.updater, "asset_name", return_value="yt-dlp_linux"), \
                mock.patch.object(self.updater, "_download", self.fake_download(digest)), \
                mock.patch.object(self.updater, "_installed_version", return_value=installed):
            self.updater.fetch_and_apply(self.cfg, NEW)

    def make_old_versions(self, names: list[str]) -> None:
        for i, name in enumerate(names):
            d = self.tmp / "versions" / name
            d.mkdir(parents=True)
            os.utime(d, (1000 + i, 1000 + i))

    def test_apply_switches_current_and_prunes_old_versions(self) -> None:
        self.make_old_versions(["v1", "v2", "v3"])
        self.apply()
        link = self.tmp / "current"
        self.assertTrue(link.is_symlink())
        self.assertEqual(os.readlink(link), str(Path("versions") / NEW))
        self.assertEqual(sorted(p.name for p in (self.tmp / "versions").iterdir()), [NEW, "v3"])
        self.assertEqual([p.name for p in (self.tmp / "versions" / NEW).iterdir()], ["yt-dlp"])
        self.assertFalse((self.tmp / ".current.tmp").exists())

    def test_checksum_or_version_mismatch_is_not_applied(self) -> None:
        self.make_old_versions(["v1"])
        (self.tmp / "current").symlink_to(Path("versions") / "v1")
        with self.assertRaises(RuntimeError):
            self.apply(digest="0" * 64)
        with self.assertRaises(RuntimeError):
            self.apply(installed="other")
        self.assertEqual(os.readlink(self.tmp / "current"), str(Path("versions") / "v1"))

    def test_failure_keeps_current_and_uses_retry_interval(self) -> None:
        self.make_old_versions(["v1"])
        (self.tmp / "current").symlink_to(Path("versions") / "v1")
        with mock.patch.object(self.updater, "latest_tag", side_effect=OSError("down")):
            self.updater.check_and_update(self.redis, self.cfg)
        self.assertEqual(os.readlink(self.tmp / "current"), str(Path("versions") / "v1"))
        state = self.redis.hgetall(self.updater.UPDATER_HASH_KEY)
        self.assertIn("down", state["last_error"])
        # 失敗の後は、通常の間隔 (21600 秒) より短い再確認の間隔 (1800 秒) で再確認する
        state_key = self.updater.UPDATER_HASH_KEY
        checked = time.time() - 2000
        self.redis.hset(state_key, mapping={"checked_at": str(checked), "error_at": str(checked)})
        with mock.patch.object(self.updater, "latest_tag", side_effect=OSError("down")) as latest:
            self.updater.check_and_update(self.redis, self.cfg)
        latest.assert_called_once()
        self.redis.hset(state_key, mapping={"checked_at": str(checked), "error_at": "0"})
        with mock.patch.object(self.updater, "latest_tag") as latest:
            self.updater.check_and_update(self.redis, self.cfg)
        latest.assert_not_called()

    def test_successful_update_records_state(self) -> None:
        with mock.patch.object(self.updater, "latest_tag", return_value=NEW), \
                mock.patch.object(self.updater, "fetch_and_apply") as fetch:
            self.updater.check_and_update(self.redis, self.cfg)
        fetch.assert_called_once_with(self.cfg, NEW)
        state = self.redis.hgetall(self.updater.UPDATER_HASH_KEY)
        self.assertEqual(state["current"], NEW)
        self.assertTrue(float(state["checked_at"]) > 0)
        self.assertTrue(float(state["updated_at"]) > 0)
        self.assertEqual(state["last_error"], "")

    def test_initial_version_only_when_empty_and_failure_propagates(self) -> None:
        with mock.patch.object(self.updater, "latest_tag", return_value=NEW) as latest, \
                mock.patch.object(self.updater, "fetch_and_apply") as fetch:
            self.updater.ensure_initial_version(self.cfg)
            fetch.assert_called_once_with(self.cfg, NEW)
            (self.tmp / "current").symlink_to(Path("versions") / NEW)
            (self.tmp / "versions" / NEW).mkdir(parents=True)
            latest.reset_mock()
            fetch.reset_mock()
            self.updater.ensure_initial_version(self.cfg)
            latest.assert_not_called()
            fetch.assert_not_called()
        (self.tmp / "current").unlink()
        with mock.patch.object(self.updater, "latest_tag", side_effect=OSError("down")), \
                self.assertRaises(OSError):
            self.updater.ensure_initial_version(self.cfg)

    def test_check_is_one_head_request_to_github_only(self) -> None:
        seen: list = []

        class Resp:
            def __enter__(self) -> "Resp":
                return self

            def __exit__(self, *a: object) -> None:
                return None

            def geturl(self) -> str:
                return f"https://github.com/yt-dlp/yt-dlp/releases/tag/{NEW}"

        def urlopen(req: object, timeout: float | None = None) -> Resp:  # noqa: ARG001
            seen.append(req)
            return Resp()

        with mock.patch("urllib.request.urlopen", urlopen):
            self.assertEqual(self.updater.latest_tag("yt-dlp/yt-dlp"), NEW)
        self.assertEqual(len(seen), 1)
        self.assertEqual(seen[0].get_method(), "HEAD")
        self.assertTrue(seen[0].full_url.startswith("https://github.com/"))
        source = (ROOT / "workerServer/src/updater.py").read_text(encoding="utf-8")
        self.assertNotIn("pypi.org", source)
        self.assertNotIn("api.github.com", source)

    def test_config_from_env_and_defaults(self) -> None:
        with mock.patch.dict(os.environ, {}, clear=True):
            cfg = self.updater.UpdaterConfig.from_env()
        self.assertEqual((cfg.update_interval, cfg.update_retry_interval, cfg.update_cooldown,
                          cfg.keep_versions, cfg.repo), (21600, 1800, 1800, 2, "yt-dlp/yt-dlp"))
        env = {"YTDLP_DIR": "/x", "YTDLP_REPO": "a/b", "UPDATE_INTERVAL": "10",
               "UPDATE_RETRY_INTERVAL": "20", "UPDATE_COOLDOWN": "30", "KEEP_VERSIONS": "5"}
        with mock.patch.dict(os.environ, env, clear=True):
            cfg = self.updater.UpdaterConfig.from_env()
        self.assertEqual((str(cfg.ytdlp_dir), cfg.repo, cfg.update_interval, cfg.update_retry_interval,
                          cfg.update_cooldown, cfg.keep_versions), ("/x", "a/b", 10, 20, 30, 5))

    def test_search_path_prefers_install_dir_over_bundled_version(self) -> None:
        for name in ("workerServer", "apiServer"):
            text = (ROOT / name / "Dockerfile").read_text(encoding="utf-8")
            m = re.search(r'ENV PATH="([^"]+)"', text)
            self.assertIsNotNone(m, name)
            parts = m.group(1).split(":")
            self.assertLess(parts.index("/opt/ytdlp/current"), parts.index("/opt/ytdlp-image/current"))


if __name__ == "__main__":
    unittest.main()
