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
JOBS_PREFIX = "ytdlp:job:"
BRPOP_TIMEOUT = int(os.environ.get("BRPOP_TIMEOUT", "5"))
REDIS_TTL = int(os.environ.get("REDIS_TTL", str(7 * 24 * 60 * 60)))

redis_message="Redis client not initialized"

running = True
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
    key = JOBS_PREFIX + job_id
    mapping = {
        "status": "pending",
        "url": job.get("url", ""),
        "options": json.dumps(job.get("options", []), ensure_ascii=False),
        "savedir": job.get("savedir") or "",
        "created_at": str(time.time()),
        "fail_count": "0",
    }
    redis_client.hset(key, mapping=mapping)
    try:
        redis_client.expire(key, REDIS_TTL)
    except Exception:
        print("WARN: Failed to set TTL for", key)
    return key


def update_status(key: str, status: str, extra: dict[str, Any] | None = None) -> None:
    if redis_client is None:
        raise RuntimeError(redis_message)
    mapping: dict[str, str] = {"status": status}
    if extra:
        for k, v in extra.items():
            mapping[k] = str(v)
    redis_client.hset(key, mapping=mapping)


def handle_job(raw: str) -> None:
    try:
        job = json.loads(raw)
    except Exception:
        print("ERROR: Failed to parse job JSON:", raw)
        return

    job_id = uuid.uuid4().hex
    key = make_job_hash(job_id, job)

    # mark in_progress
    update_status(key, "in_progress", {"started_at": str(time.time())})

    ok, output = run_yt_dlp(job)

    if ok:
        update_status(
            key, "completed", {"completed_at": str(time.time()), "output": output})
    else:
        # increment fail_count
        try:
            redis_client.hincrby(key, "fail_count", 1)
        except Exception:
            print("ERROR: Failed to increment fail_count for", key)
        update_status(key, "failed", {"error": output, "failed_at": str(time.time())})


def main() -> int:
    print("INFO: Worker started, listening on queue:", QUEUE_KEY)

    while running:
        try:
            item = redis_client.brpop(QUEUE_KEY, timeout=BRPOP_TIMEOUT)
            if not item:
                continue

            raw = item[1]
            handle_job(raw)
        except redis.RedisError:
            print(
                "ERROR: Redis error while popping/processing job, retrying after delay")
            time.sleep(1)
        except Exception:
            print("ERROR: Unexpected error in worker loop")
            time.sleep(1)

    print("INFO: Worker stopped gracefully")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        # Suppress full traceback on Ctrl-C; print concise message and exit
        print("INFO: KeyboardInterrupt received — shutting down worker gracefully")
        sys.exit(0)
