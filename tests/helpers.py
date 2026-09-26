"""テスト用ヘルパ。API/Worker で同名モジュール(main/function/cookies)が衝突するため、
サービスごとに sys.modules を入れ替えて読み込む。"""
from __future__ import annotations

import importlib
import os
import sys
import tempfile
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
_NAMES = ("main", "function", "cookies")


class FakeRedis:
    """テストに必要な最小限の Redis 互換(hash / list / scan)。"""

    def __init__(self) -> None:
        self.hashes: dict[str, dict[str, str]] = {}
        self.lists: dict[str, list[str]] = {}

    def ping(self) -> bool:
        return True

    def hset(self, key: str, mapping: dict | None = None, **kw: str) -> int:
        self.hashes.setdefault(key, {}).update({k: str(v) for k, v in (mapping or kw).items()})
        return 1

    def hget(self, key: str, field: str) -> str | None:
        return self.hashes.get(key, {}).get(field)

    def hgetall(self, key: str) -> dict[str, str]:
        return dict(self.hashes.get(key, {}))

    def hincrby(self, key: str, field: str, n: int = 1) -> int:
        h = self.hashes.setdefault(key, {})
        h[field] = str(int(h.get(field, "0")) + n)
        return int(h[field])

    def expire(self, *_a: object) -> bool:
        return True

    def delete(self, key: str) -> int:
        return int(self.hashes.pop(key, None) is not None)

    def scan_iter(self, match: str = "*") -> list[str]:
        prefix = match.rstrip("*")
        return [k for k in self.hashes if k.startswith(prefix)]

    def rpush(self, key: str, *vals: str) -> int:
        self.lists.setdefault(key, []).extend(vals)
        return len(self.lists[key])

    def lpush(self, key: str, *vals: str) -> int:
        self.lists.setdefault(key, [])[0:0] = list(vals)
        return len(self.lists[key])


def load_service(service_dir: str, cookie_dir: str | None = None,
                 fake_redis: FakeRedis | None = None) -> object:
    """apiServer / workerServer の main モジュールを新規に読み込んで返す。"""
    os.environ["COOKIE_DIR"] = cookie_dir or tempfile.mkdtemp(prefix="cookies-test-")
    os.environ.setdefault("DEBUG", "1")  # API は Redis 無しでも起動できるようにする
    for n in _NAMES:
        sys.modules.pop(n, None)
    src = str(ROOT / service_dir / "src")
    sys.path.insert(0, src)
    try:
        with mock.patch("redis.Redis.from_url", return_value=fake_redis or FakeRedis()):
            return importlib.import_module("main")
    finally:
        sys.path.remove(src)
