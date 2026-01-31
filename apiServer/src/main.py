from __future__ import annotations

import json
import os
import re
import sys
import threading
import time
import unicodedata

import redis
from flask import Flask, jsonify, request
from waitress import serve

import function

app = Flask(__name__)
app.json.ensure_ascii = False

# Redis client (configured via REDIS_URL environment variable)
REDIS_URL = os.environ.get("REDIS_URL", "redis://localhost:6379")
# Debug mode: if set (any non-empty value), allow startup without Redis
DEBUG_MODE = bool(os.environ.get("DEBUG"))
PORT = int(os.environ.get("PORT", "5000"))

QUEUE_PREFIX_BASE = "ytdlp:queue"
REQUESTS_PREFIX_BASE = "ytdlp:requests"
JOBS_PREFIX_BASE = "ytdlp:jobs"

try:
    redis_client = redis.Redis.from_url(REDIS_URL, decode_responses=True)
    # quick health check
    redis_client.ping()
    print("INFO: Connected to Redis at", REDIS_URL)
except Exception as e:
    if DEBUG_MODE:
        redis_client = None
        print("WARNING: Could not connect to Redis (debug mode) — continuing:", e)
    else:
        print("ERROR: Could not connect to Redis:", e)
        sys.exit(1)


class ParameterError(Exception):
    def __init__(self: Exception) -> None:
        return


def parse_request(form: dict) -> tuple[str, list, str | None]:
    if not isinstance(form, dict):
        raise ParameterError

    url = form.get("url")
    savedir = form.get("savedir")
    savedir = unicodedata.normalize("NFC", savedir)
    savedir = re.sub(r'[\\/¥:*?"<>|]', "_", savedir)
    savedir = re.sub(r"\s+", " ", savedir.replace("\u3000", "")).strip()

    # Validate url
    if not isinstance(url, str) or url.strip() == "":
        raise ParameterError

    # `options` must be provided as a single string in the API input (or omitted)
    raw_options = form.get("options")

    if DEBUG_MODE:
        print(f"DEBUG: raw_options={raw_options!r}")

    if raw_options is None:
        options: list[str] = []
    elif not isinstance(raw_options, str):
        # Strict API: options must be string
        raise ParameterError
    else:
        # split on whitespace and remove empty segments
        options = [p for p in raw_options.split() if p != ""]

    # Validate savedir if provided
    if savedir is not None and not isinstance(savedir, str):
        raise ParameterError

    return url, options, savedir

def probe_jobs(url: str, options: list, savedir: str | None) -> list[dict]:
    try:
        return function.probe_and_build_jobs(url, options, savedir)
    except RuntimeError as e:
        msg = str(e)
        print("ERROR: probe failed:", msg)
        raise


def add_request(url: str, options: list, savedir: str | None) -> None:
    key = f"{REQUESTS_PREFIX_BASE}"
    try:
        payload = json.dumps({
            "url": url, "options": options, "savedir": savedir},
            ensure_ascii=False)
        redis_client.rpush(key, payload)
        print("INFO: Add Request to Redis.")
    except Exception as e:
        print("WARNING: Failed to push request to Redis:", e)
        raise

def push_jobs(jobs: list[dict]) -> int:
    entries = [json.dumps(job, ensure_ascii=False) for job in jobs]
    if DEBUG_MODE:
        # print entries for debugging
        print(f"DEBUG JOB: {entries}")

    if entries:
        try:
            redis_client.rpush(QUEUE_PREFIX_BASE, *entries)
            print("INFO: Pushed", len(entries), "jobs to Redis.")
        except AttributeError:
            print("WARNING: Redis client is down; not pushing jobs.")
            raise
        except Exception as e:
            print("WARNING: Failed to push jobs to Redis:", e)
            raise

    return len(entries)

def handle_download(url: str, options: list, savedir: str | None) -> tuple[dict, int]:
    try:
        jobs = probe_jobs(url, options, savedir)
    except RuntimeError:
        print("ERROR: yt-dlp probe failed — process exit.")

        def _exit_later(delay: float = 0.5) -> None:
            time.sleep(delay)
            print("INFO: Exiting process now (yt-dlp probe failure).")
            os._exit(1)

        t = threading.Thread(target=_exit_later, args=(0.5,), daemon=True)
        t.start()

        return jsonify(
            {"message": "yt-dlp probe failed; wait restart yt-dlp."}), 400
    except Exception as e:
        print("ERROR: unexpected error during probe:", e, " jobs: ",
              json.dumps(jobs, ensure_ascii=False))
        return jsonify({"message": "Internal server error. "}), 500

    try:
        push_jobs(jobs)
    except Exception as e:
        print("ERROR: unexpected error during push jobs: ", e, " jobs: ",
              json.dumps(jobs, ensure_ascii=False))
        return jsonify({"message": "Internal server error."}), 500

    return jsonify({"message": "Request accepted."}), 200

@app.route("/download", methods=["POST"])
def endpoint() -> tuple[dict, int]:
    # Use module-level helpers to keep this function small

    # endpoint main flow
    form = request.json
    try:
        url, options, savedir = parse_request(form)
    except ParameterError:
        print("Error: Invalid request requested.")
        return jsonify({"message": "Invalid request."}), 400
    if DEBUG_MODE:
        print(f"DEBUG REQUEST: url={url}, options={options}, savedir={savedir}")

    return handle_download(url, options, savedir)

@app.route("/schedule", methods=["POST"])
def schedule_endpoint() -> tuple[dict, int]:
    form = request.json
    try:
        url, options, savedir = parse_request(form)
    except ParameterError:
        print("Error: Invalid scheduled request.")
        return jsonify({"message": "Invalid request."}), 400

    try:
        add_request(url, options, savedir)
    except Exception:
        return jsonify({"message": "Internal server error."}), 500

    return jsonify({"message": "Request scheduled."}), 200


@app.route("/schedule", methods=["GET"])
def get_scheduled_requests() -> tuple[dict, int]:
    key = REQUESTS_PREFIX_BASE

    if redis_client is None:
        return jsonify({"message": "Redis not available."}), 500

    try:
        entries = redis_client.lrange(key, 0, -1)
    except Exception as e:
        print("ERROR: failed to read scheduled requests:", e)
        return jsonify({"message": "Internal server error."}), 500

    results: list[dict] = []
    for entry in entries:
        try:
            req = json.loads(entry)
        except Exception:
            print("WARNING: failed to parse stored request; skipping", entry)
            continue

        # Exclude `options` from API response
        if isinstance(req, dict):
            req.pop("options", None)
            results.append(req)

    return jsonify(results), 200


@app.route("/download/scheduled", methods=["POST"])
def download_scheduled() -> tuple[dict, int]:
    key = REQUESTS_PREFIX_BASE
    processed = 0

    if redis_client is None:
        return jsonify({"message": "Redis not available."}), 500

    # Read optional body: {"count": XX} or {"count": "all"}
    form = request.json or {}
    count_raw = form.get("count")
    process_all = False
    count_limit = 0

    if count_raw is None or (isinstance(count_raw, str) and count_raw == "all"):
        process_all = True
    else:
        try:
            count_limit = max(int(count_raw), 1)
        except Exception:
            return jsonify({"message": "Invalid count parameter."}), 400

    try:
        while True:
            if not process_all and processed >= count_limit:
                break

            entry = redis_client.lpop(key)
            if entry is None:
                break

            try:
                req = json.loads(entry)
            except Exception:
                print("WARNING: Failed to parse scheduled request; skipping:", entry)
                continue

            url = req.get("url")
            options = req.get("options") or []
            savedir = req.get("savedir")

            msg, code = handle_download(url, options, savedir)
            if code != 200:
                raise Exception(msg.get("message", "Unknown error"))

            processed += 1
    except Exception as e:
        print("ERROR: failed processing scheduled requests:", e)
        return jsonify({"message": "Internal server error."}), 500

    return jsonify({
        "message": "Processed scheduled requests.", "count": str(processed)}), 200


@app.route("/download/retry", methods=["POST"])
def retry_failed_jobs() -> tuple[dict, int]:
     if redis_client is None:
         return jsonify({"message": "Redis not available."}), 500

     primary_pattern = f"{JOBS_PREFIX_BASE}:failed:*"
     reset_count = 0

     try:
         # use scan_iter for safe iteration
         for key in redis_client.scan_iter(match=primary_pattern):
             try:
                 # try hash map
                 data = redis_client.hgetall(key)
                 if data:
                     # hset mapping expects strings
                     data["failed_count"] = "0"
                     redis_client.hset(key, mapping=data)
                     reset_count += 1
                     continue
                 # unknown format, skip
                 print("WARNING: unknown job data format for key:", key)
             except Exception as e:
                 print("WARNING: failed to reset failed_count for key", key, e)
                 continue
     except Exception as e:
         print("ERROR: failed scanning failed jobs:", e)
         return jsonify({"message": "Internal server error."}), 500

     return jsonify({
         "message": "Reset failed_count for failed jobs.", "count": reset_count}), 200

if __name__ == "__main__":
    print("INFO: Start ytdlpServer port:", PORT)
    serve(app, host="0.0.0.0", port=PORT)
