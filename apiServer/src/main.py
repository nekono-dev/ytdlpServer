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


def push_jobs(jobs: list[dict]) -> int:
    entries = [json.dumps(job, ensure_ascii=False) for job in jobs]
    if DEBUG_MODE:
        # print entries for debugging
        print(f"DEBUG JOB: {entries}")

    if entries:
        try:
            redis_client.rpush("ytdlp:queue", *entries)
            print("INFO: Pushed", len(entries), "jobs to Redis.")
        except AttributeError:
            print("WARNING: Redis client is down; not pushing jobs.")
            raise
        except Exception as e:
            print("WARNING: Failed to push jobs to Redis:", e)
            raise

    return len(entries)


@app.route("/ytdlp", methods=["POST"])
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
        print("ERROR: unexpected error during probe:", e)
        return jsonify({"message": "Internal server error."}), 500

    try:
        push_jobs(jobs)
    except Exception:
        return jsonify({"message": "Internal server error."}), 500

    return jsonify({"message": "Request accepted."}), 200


if __name__ == "__main__":
    print("INFO: Start ytdlpServer port:", PORT)
    serve(app, host="0.0.0.0", port=PORT)
