"""ジョブの状態遷移・取得・回収。worker (main.py) と dispatcher (dispatcher.py) が共有する。

設計は specs/workerServer/design.md の「イベント駆動の worker 起動、yt-dlp の更新、
ライフサイクル」を参照。

キー:
  ytdlp:queue                 list   API が RPUSH するキュー
  ytdlp:queue:dead            list   解析できなかったジョブ (調査用に退避)
  ytdlp:processing:<worker>   list   取り出したが in_progress の記録をまだ作っていないジョブ (最大 1 要素)
  ytdlp:workers:<worker>      string (TTL) worker の生存を示すリース
  ytdlp:jobs:<status>:<id>    hash   ジョブの記録 (status はキー名にも埋め込む)
"""
from __future__ import annotations

import json
import time
from typing import TYPE_CHECKING, Any

import redis as redis_module

if TYPE_CHECKING:
    from collections.abc import Iterator

import cookies

JOBS_PREFIX_BASE = "ytdlp:jobs"
QUEUE_KEY = "ytdlp:queue"
DEAD_QUEUE_KEY = "ytdlp:queue:dead"
PROCESSING_PREFIX = "ytdlp:processing"
WORKER_LEASE_PREFIX = "ytdlp:workers"

INTERRUPTED_ERROR_CODE = "interrupted"


def _to_str(v: Any) -> str:
    if isinstance(v, (list, dict)):
        return json.dumps(v, ensure_ascii=False)
    return "" if v is None else str(v)


def processing_key(worker_id: str) -> str:
    return f"{PROCESSING_PREFIX}:{worker_id}"


def lease_key(worker_id: str) -> str:
    return f"{WORKER_LEASE_PREFIX}:{worker_id}"


def in_progress_key(job_id: str) -> str:
    return f"{JOBS_PREFIX_BASE}:in_progress:{job_id}"


def failed_key(job_id: str) -> str:
    return f"{JOBS_PREFIX_BASE}:failed:{job_id}"


# ---- リース (worker の生存) -------------------------------------------------

def acquire_lease(client: redis_module.Redis, worker_id: str, ttl: int) -> None:
    client.set(lease_key(worker_id), "1", ex=ttl)


def renew_lease(client: redis_module.Redis, worker_id: str, ttl: int) -> None:
    client.set(lease_key(worker_id), "1", ex=ttl)


def release_lease(client: redis_module.Redis, worker_id: str) -> None:
    client.delete(lease_key(worker_id))


def lease_alive(client: redis_module.Redis, worker_id: str) -> bool:
    return bool(client.exists(lease_key(worker_id)))


# ---- 取得 (worker) ----------------------------------------------------------

def take_from_queue(client: redis_module.Redis, worker_id: str) -> str | None:
    """キューから 1 件を processing リストへ移す (LMOVE)。無ければ None。"""
    return client.lmove(QUEUE_KEY, processing_key(worker_id), "LEFT", "RIGHT")


def commit_from_processing(
        client: redis_module.Redis, worker_id: str, job_id: str,
        job: dict[str, Any], ttl: int) -> str:
    """processing リストのジョブを in_progress の記録にする。"""
    key = in_progress_key(job_id)
    mapping = {
        "status": "in_progress",
        "url": _to_str(job.get("url", "")),
        "options": _to_str(job.get("options", [])),
        "savedir": _to_str(job.get("savedir") or ""),
        "created_at": _to_str(job.get("created_at") or time.time()),
        "failed_count": _to_str(job.get("failed_count") or 0),
        "filename": _to_str(job.get("filename") or job_id),
        "auth_profile": _to_str(job.get("auth_profile") or ""),
        "worker_id": worker_id,
        "started_at": _to_str(time.time()),
    }
    client.hset(key, mapping=mapping)
    try:
        client.expire(key, ttl)
    except Exception:
        print("WARNING: Failed to set TTL for", key)
    client.delete(processing_key(worker_id))
    return key


def discard_processing(client: redis_module.Redis, worker_id: str, raw: str) -> None:
    """解析できなかったジョブを退避し、processing リストを片付ける。"""
    client.rpush(DEAD_QUEUE_KEY, raw)
    client.delete(processing_key(worker_id))


def _matches_retry(
        client: redis_module.Redis, data: dict[str, str], retry_count: int) -> bool:
    try:
        cnt = int(data.get("failed_count") or 0)
    except Exception:
        cnt = 0
    if cnt >= retry_count:
        return False
    return not is_waiting_for_login(client, data)


def iter_retryable_failed_keys(
        client: redis_module.Redis, retry_count: int) -> Iterator[str]:
    """failed_count < retry_count かつログイン待ちでない failed キーを列挙する。"""
    pattern = f"{JOBS_PREFIX_BASE}:failed:*"
    try:
        for k in client.scan_iter(match=pattern, count=200):
            try:
                data = client.hgetall(k) or {}
            except Exception:
                continue
            if _matches_retry(client, data, retry_count):
                yield k
    except Exception:
        return


def count_pending_targets(
        client: redis_module.Redis, retry_count: int, cap: int) -> int:
    """dispatcher の起動判断用: キュー長 + リトライ対象数 (cap で打ち切る概数)。"""
    n = 0
    try:
        n = client.llen(QUEUE_KEY) or 0
    except Exception:
        n = 0
    if n >= cap:
        return n
    for _ in iter_retryable_failed_keys(client, retry_count):
        n += 1
        if n >= cap:
            break
    return n


def take_retryable_failed(
        client: redis_module.Redis, retry_count: int, worker_id: str) -> str | None:
    """リトライ対象の failed ジョブを 1 件、in_progress へ原子的に取得する。"""
    for key in iter_retryable_failed_keys(client, retry_count):
        job_id = key.split(":")[-1]
        new_key = in_progress_key(job_id)
        try:
            renamed = client.renamenx(key, new_key)
        except redis_module.exceptions.ResponseError:
            # 他の worker が先に取得済み (src が既に無い) など
            continue
        if not renamed:
            continue
        client.hset(new_key, mapping={
            "status": "in_progress",
            "worker_id": worker_id,
            "started_at": _to_str(time.time()),
        })
        return new_key
    return None


def is_waiting_for_login(client: redis_module.Redis, data: dict[str, str]) -> bool:
    """ログイン要求で失敗し、cookie がまだ有効に戻っていないジョブか。

    cookie を入れ直す(valid に戻る)まで再試行しない。
    プロファイル未指定のログイン要求は再試行しても解決しないため常に対象外。
    """
    if data.get("error_code") != "login_required":
        return False
    profile = data.get("auth_profile") or ""
    if not profile:
        return True
    return cookies.profile_state(client, profile) != "valid"


# ---- 完了・失敗の記録 (所有者チェック付き) ----------------------------------

def is_owner(client: redis_module.Redis, key: str, worker_id: str) -> bool:
    try:
        owner = client.hget(key, "worker_id")
    except Exception:
        return False
    return owner == worker_id


def update_status(
        client: redis_module.Redis, key: str, status: str,
        extra: dict[str, Any] | None, ttl: int) -> str:
    """`ytdlp:jobs:<status>:<id>` へキーを移し、項目を更新する (既存仕様どおり)。"""
    try:
        job_id = key.split(":")[-1]
    except Exception:
        job_id = key
    new_key = f"{JOBS_PREFIX_BASE}:{status}:{job_id}"

    if key == new_key:
        mapping: dict[str, str] = {"status": _to_str(status)}
        if extra:
            for k, v in extra.items():
                mapping[k] = _to_str(v)
        client.hset(key, mapping=mapping)
        try:
            client.expire(key, ttl)
        except Exception:
            print("WARNING: Failed to set TTL for", key)
        return key

    try:
        existing = client.hgetall(key) or {}
    except Exception:
        existing = {}

    mapping = {k: _to_str(v) for k, v in existing.items()}
    mapping["status"] = _to_str(status)
    if extra:
        for k, v in extra.items():
            mapping[k] = _to_str(v)

    client.hset(new_key, mapping=mapping)
    try:
        client.expire(new_key, ttl)
    except Exception:
        print("WARNING: Failed to set TTL for", new_key)

    try:
        if key != new_key:
            client.delete(key)
    except Exception:
        print("WARNING: Failed to delete old key", key)

    return new_key


def mark_interrupted(
        client: redis_module.Redis, key: str, *, consume_retry: bool,
        reason: str, ttl: int) -> str:
    """中断 (停止指示・異常終了) を failed へ記録する。

    consume_retry=False (停止指示) では failed_count を増やさない (W24)。
    consume_retry=True (異常終了・回収) では増やす (W23, W17)。
    """
    if consume_retry:
        try:
            client.hincrby(key, "failed_count", 1)
        except Exception:
            print("ERROR: Failed to increment failed_count for", key)
    extra = {
        "error_code": INTERRUPTED_ERROR_CODE,
        "error": reason,
        "failed_at": str(time.time()),
    }
    return update_status(client, key, "failed", extra, ttl)


# ---- 異常終了・停止済み worker の回収 (dispatcher) --------------------------

def reclaim_stale(
        client: redis_module.Redis, *, inprogress_stale: int, ttl: int) -> int:
    """リースの無い in_progress / processing を回収する。回収した件数を返す。"""
    reclaimed = 0
    now = time.time()

    # processing (取得済みだが in_progress 未作成) を先に処理する。
    # in_progress の回収より先に見ることで、「commit 済みで processing の削除だけが
    # 取り残された」二重コピーを、その in_progress がまだ存在するうちに判定できる
    # (順序を逆にすると、同じ worker の in_progress が先に failed へ移されてしまい、
    # 誤って重複してキューへ戻してしまう)。
    pattern = f"{PROCESSING_PREFIX}:*"
    try:
        keys = list(client.scan_iter(match=pattern, count=200))
    except Exception:
        keys = []
    for key in keys:
        worker_id = key.split(":")[-1]
        if lease_alive(client, worker_id):
            continue
        try:
            raw_list = client.lrange(key, 0, -1)
        except Exception:
            raw_list = []
        if not raw_list:
            client.delete(key)
            continue
        raw = raw_list[0]
        job_id = None
        try:
            job_id = json.loads(raw).get("id")
        except Exception:
            job_id = None
        if job_id and client.exists(in_progress_key(job_id)):
            # commit 済みで processing の削除だけが取り残された (二重コピー)
            client.delete(key)
            continue
        # まだ in_progress の記録が無いので、キューの先頭へ戻す (順序を保つ)
        client.lmove(key, QUEUE_KEY, "RIGHT", "LEFT")
        client.delete(key)
        reclaimed += 1

    pattern = f"{JOBS_PREFIX_BASE}:in_progress:*"
    try:
        keys = list(client.scan_iter(match=pattern, count=200))
    except Exception:
        keys = []
    for key in keys:
        try:
            data = client.hgetall(key) or {}
        except Exception:
            continue
        worker_id = data.get("worker_id") or ""
        if worker_id:
            if lease_alive(client, worker_id):
                continue
        else:
            try:
                started_at = float(data.get("started_at") or 0)
            except ValueError:
                started_at = 0
            if now - started_at <= inprogress_stale:
                continue
        mark_interrupted(
            client, key, consume_retry=True,
            reason="worker did not renew its lease (reclaimed)", ttl=ttl)
        reclaimed += 1

    return reclaimed
