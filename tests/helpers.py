"""テスト用ヘルパ。API/Worker で同名モジュール(main/function/cookies)が衝突するため、
サービスごとに sys.modules を入れ替えて読み込む。"""
from __future__ import annotations

import importlib
import os
import sys
import tempfile
from pathlib import Path
from unittest import mock

import redis as redis_module

ROOT = Path(__file__).resolve().parent.parent
_NAMES = (
    "main", "function", "cookies", "session", "browser", "netscape",
    "jobs", "updater", "dispatcher", "backends")


class FakeRedis:
    """テストに必要な最小限の Redis 互換(hash / list / string / scan)。"""

    def __init__(self) -> None:
        self.hashes: dict[str, dict[str, str]] = {}
        self.lists: dict[str, list[str]] = {}
        self.strings: dict[str, str] = {}
        self.published: list[tuple[str, str]] = []

    def ping(self) -> bool:
        return True

    # ---- hash ----------------------------------------------------------

    def hset(self, key: str, field: str | None = None, value: str | None = None,
              mapping: dict | None = None) -> int:
        h = self.hashes.setdefault(key, {})
        if mapping is not None:
            created = sum(1 for k in mapping if k not in h)
            h.update({k: str(v) for k, v in mapping.items()})
            return created
        created = 0 if field in h else 1
        h[str(field)] = str(value)
        return created

    def hget(self, key: str, field: str) -> str | None:
        return self.hashes.get(key, {}).get(field)

    def hgetall(self, key: str) -> dict[str, str]:
        return dict(self.hashes.get(key, {}))

    def hincrby(self, key: str, field: str, n: int = 1) -> int:
        h = self.hashes.setdefault(key, {})
        h[field] = str(int(h.get(field, "0")) + n)
        return int(h[field])

    # ---- string (リース) -------------------------------------------------

    def set(self, key: str, value: str, ex: int | None = None) -> bool:  # noqa: ARG002
        self.strings[key] = str(value)
        return True

    def get(self, key: str) -> str | None:
        return self.strings.get(key)

    # ---- 汎用 ------------------------------------------------------------

    def expire(self, *_a: object) -> bool:
        return True

    def exists(self, key: str) -> int:
        return int(key in self.hashes or key in self.lists or key in self.strings)

    def delete(self, *keys: str) -> int:
        n = 0
        for key in keys:
            n += int(self.hashes.pop(key, None) is not None)
            n += int(self.lists.pop(key, None) is not None)
            n += int(self.strings.pop(key, None) is not None)
        return n

    def scan_iter(self, match: str = "*", count: int = 10) -> list[str]:  # noqa: ARG002
        prefix = match.rstrip("*")
        keys = set(self.hashes) | set(self.lists) | set(self.strings)
        return [k for k in keys if k.startswith(prefix)]

    def rename(self, src: str, dst: str) -> bool:
        if src not in self.hashes:
            msg = "no such key"
            raise redis_module.exceptions.ResponseError(msg)
        self.hashes[dst] = self.hashes.pop(src)
        return True

    def renamenx(self, src: str, dst: str) -> bool:
        if src not in self.hashes:
            msg = "no such key"
            raise redis_module.exceptions.ResponseError(msg)
        if dst in self.hashes:
            return False
        self.hashes[dst] = self.hashes.pop(src)
        return True

    # ---- list ------------------------------------------------------------

    def rpush(self, key: str, *vals: str) -> int:
        self.lists.setdefault(key, []).extend(vals)
        return len(self.lists[key])

    def lpush(self, key: str, *vals: str) -> int:
        self.lists.setdefault(key, [])[0:0] = list(vals)
        return len(self.lists[key])

    def lpop(self, key: str) -> str | None:
        lst = self.lists.get(key)
        if not lst:
            return None
        v = lst.pop(0)
        if not lst:
            self.lists.pop(key, None)
        return v

    def llen(self, key: str) -> int:
        return len(self.lists.get(key, []))

    def lrange(self, key: str, start: int, end: int) -> list[str]:
        lst = self.lists.get(key, [])
        if end == -1:
            return list(lst[start:])
        return list(lst[start:end + 1])

    def lmove(
            self, src: str, dst: str, wherefrom: str, whereto: str) -> str | None:
        lst = self.lists.get(src)
        if not lst:
            return None
        v = lst.pop(0 if wherefrom.upper() == "LEFT" else -1)
        if not lst:
            self.lists.pop(src, None)
        dst_list = self.lists.setdefault(dst, [])
        if whereto.upper() == "LEFT":
            dst_list.insert(0, v)
        else:
            dst_list.append(v)
        return v

    # ---- pub/sub (通知の記録のみ。購読は行わない) -------------------------

    def publish(self, channel: str, message: str) -> int:
        self.published.append((channel, message))
        return 0


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
