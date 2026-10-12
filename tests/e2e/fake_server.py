"""操作ページの E2E 用。実ブラウザの代わりに偽のブラウザで、制御 API を起動する。"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from aiohttp import web
from helpers import load_service
from test_browser import FakeBrowser, reset_fake

PORT = 18080


def main() -> None:
    module = load_service("browserServer", tempfile.mkdtemp())
    reset_fake()
    manager = sys.modules["session"].SessionManager(FakeBrowser, timeout=3600)
    web.run_app(module.create_app(manager), host="127.0.0.1", port=PORT, print=None)


if __name__ == "__main__":
    main()
