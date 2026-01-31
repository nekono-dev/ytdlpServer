from __future__ import annotations

import json
import os
import sys
import time
import uuid
from typing import Any

import redis

from function import run_yt_dlp

REDIS_URL = os.environ.get("REDIS_URL", "redis://localhost:6379")
QUEUE_KEY = "ytdlp:queue"
# New spec: job status keys include status in key name
# Format: ytdlp:jobs:<status>:<job_id>
JOBS_PREFIX_BASE = "ytdlp:jobs"
BRPOP_TIMEOUT = int(os.environ.get("BRPOP_TIMEOUT", "60"))
REDIS_TTL = int(os.environ.get("REDIS_TTL", str(7 * 24 * 60 * 60)))
RETRY_COUNT = int(os.environ.get("RETRY_COUNT", "5"))

redis_message="Redis client not initialized"

redis_client: redis.Redis | None = None

try:
    redis_client = redis.Redis.from_url(REDIS_URL, decode_responses=True)
    redis_client.ping()
    print("INFO: Connected to Redis:", REDIS_URL)
except Exception as e:
    print("ERROR: Failed to connect to Redis:", e)
    # Exit immediately so orchestration can restart/update configuration
    sys.exit(1)

def make_job_hash(job_id: str, job: dict[str, Any]) -> str:
    if redis_client is None:
        raise RuntimeError(redis_message)
    key = f"{JOBS_PREFIX_BASE}:pending:{job_id}"
    mapping = {
        "status": "pending",
        "url": job.get("url", ""),
        "options": json.dumps(job.get("options", []), ensure_ascii=False),
        "savedir": job.get("savedir") or "",
        "created_at": str(time.time()),
        "failed_count": "0",
        "filename": job.get("filename") or "",
    }
    redis_client.hset(key, mapping=mapping)
    try:
        redis_client.expire(key, REDIS_TTL)
    except Exception:
        print("WARN: Failed to set TTL for", key)
    return key


def update_status(key: str, status: str, extra: dict[str, Any] | None = None) -> str:
    """
    Move job hash to a new key that embeds the `status` (per spec).

    Returns the new key name.
    """
    if redis_client is None:
        raise RuntimeError(redis_message)

    # extract job_id (works for both old and new formats)
    try:
        job_id = key.split(":")[-1]
    except Exception:
        job_id = key

    new_key = f"{JOBS_PREFIX_BASE}:{status}:{job_id}"

    # If no migration is necessary (same key), just update fields
    if key == new_key:
        mapping: dict[str, str] = {"status": status}
        if extra:
            for k, v in extra.items():
                mapping[k] = str(v)
        redis_client.hset(key, mapping=mapping)
        try:
            redis_client.expire(key, REDIS_TTL)
        except Exception:
            print("WARN: Failed to set TTL for", key)
        return key

    # Read existing data, merge updates, write to new key, then delete old key
    try:
        existing = redis_client.hgetall(key) or {}
    except Exception:
        existing = {}

    # ensure existing fields are strings
    mapping: dict[str, str] = {k: str(v) for k, v in existing.items()}
    mapping["status"] = status
    if extra:
        for k, v in extra.items():
            mapping[k] = str(v)

    redis_client.hset(new_key, mapping=mapping)
    try:
        redis_client.expire(new_key, REDIS_TTL)
    except Exception:
        print("WARN: Failed to set TTL for", new_key)

    # remove old key if exists and is different
    try:
        if key != new_key:
            redis_client.delete(key)
    except Exception:
        print("WARN: Failed to delete old key", key)

    return new_key


def handle_job(raw: str) -> None:
    try:
        job = json.loads(raw)
    except Exception:
        print("ERROR: Failed to parse job JSON:", raw)
        return

    # Prefer yt-dlp provided id if present; fallback to generated UUID
    jid = job.get("id")
    job_id = str(jid) if jid is not None else uuid.uuid4().hex
    key = make_job_hash(job_id, job)
    # mark in_progress (update_status returns the new key)
    key = update_status(key, "in_progress", {"started_at": str(time.time())})

    ok, output = run_yt_dlp(job)

    if ok:
        key = update_status(
            key, "completed", {"completed_at": str(time.time()), "output": output})
    else:
        # increment failed_count on current key, then migrate to failed
        try:
            redis_client.hincrby(key, "failed_count", 1)
        except Exception:
            print("ERROR: Failed to increment failed_count for", key)
        key = update_status(
            key, "failed", {"error": output, "failed_at": str(time.time())})


def find_retryable_failed_key() -> str | None:
    """Scan for failed keys that have failed_count < RETRY_COUNT and return one key or None."""
    if redis_client is None:
        raise RuntimeError(redis_message)

    pattern = f"{JOBS_PREFIX_BASE}:failed:*"
    try:
        for k in redis_client.scan_iter(match=pattern):
            try:
                cnt = int(redis_client.hget(k, "failed_count") or 0)
            except Exception:
                cnt = 0
            if cnt < RETRY_COUNT:
                return k
    except Exception:
        # Fallback: no retryable key found or scan failed
        return None

    return None


def process_failed_key(key: str) -> None:
    """Transition failed key -> in_progress, run yt-dlp, then mark completed/failed."""
    if redis_client is None:
        raise RuntimeError(redis_message)

    # Move to in_progress
    key = update_status(key, "in_progress", {"started_at": str(time.time())})

    # Build job dict from hash
    try:
        data = redis_client.hgetall(key) or {}
    except Exception:
        data = {}

    job = {
        "url": data.get("url", ""),
        "options": json.loads(data.get("options", "[]") or "[]"),
        "savedir": data.get("savedir", ""),
    }

    ok, output = run_yt_dlp(job)

    if ok:
        update_status(key, "completed", {"completed_at": str(time.time()), "output": output})
    else:
        try:
            redis_client.hincrby(key, "failed_count", 1)
        except Exception:
            print("ERROR: Failed to increment failed_count for", key)
        update_status(key, "failed", {"error": output, "failed_at": str(time.time())})


def main() -> int:
    print("INFO: Worker started — processing at most one job then exit")

    try:
        retry_key = find_retryable_failed_key()
        if retry_key:
            print("INFO: Found retryable failed job:", retry_key)
            process_failed_key(retry_key)
            print("INFO: Retried failed job; exiting")
            return 0

        item = redis_client.blpop(QUEUE_KEY, timeout=BRPOP_TIMEOUT)
        if item:
            raw = item[1]
            print("INFO: Pulled job from queue")
            handle_job(raw)
            print("INFO: Job from queue processed; exiting")
            return 0

    except redis.RedisError:
        print("ERROR: Redis error while acquiring job")
        return 1
    except Exception:
        print("ERROR: Unexpected error in worker")
        return 1

    print("INFO: No job found in queue or retryable failed jobs; exiting")
    return 0

if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        # Suppress full traceback on Ctrl-C; print concise message and exit
        print("INFO: KeyboardInterrupt received — shutting down worker gracefully")
        sys.exit(0)
    except Exception:
        sys.exit(0)

