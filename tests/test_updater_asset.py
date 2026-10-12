"""updater.py のバイナリ名選択の単体テスト (ytdlp-binary 仕様の受け入れ条件)。"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
SRC = str(ROOT / "workerServer" / "src")


class AssetNameTest(unittest.TestCase):
    def setUp(self) -> None:
        sys.modules.pop("updater", None)
        sys.path.insert(0, SRC)
        import updater
        self.updater = updater

    def tearDown(self) -> None:
        sys.path.remove(SRC)

    def _name(self, machine: str, *, musl: bool) -> str:
        with mock.patch("platform.machine", return_value=machine), \
                mock.patch.object(self.updater, "is_musl", return_value=musl):
            return self.updater.asset_name()

    def test_glibc(self) -> None:
        self.assertEqual(self._name("x86_64", musl=False), "yt-dlp_linux")
        self.assertEqual(self._name("aarch64", musl=False), "yt-dlp_linux_aarch64")

    def test_musl(self) -> None:
        self.assertEqual(self._name("x86_64", musl=True), "yt-dlp_musllinux")
        self.assertEqual(self._name("aarch64", musl=True), "yt-dlp_musllinux_aarch64")

    def test_unsupported(self) -> None:
        with self.assertRaises(RuntimeError):
            self._name("mips", musl=False)


if __name__ == "__main__":
    unittest.main()
