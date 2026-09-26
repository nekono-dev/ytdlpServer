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

import cookies
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
    def __init__(self: Exception, message: str = "Invalid request.") -> None:
        super().__init__(message)
        self.message = message


def parse_request(
        form: dict) -> tuple[str, list, str | None, str | None, str | None]:
    if not isinstance(form, dict):
        raise ParameterError

    url = form.get("url")
    savedir = form.get("savedir")
    if savedir is not None:
        if not isinstance(savedir, str):
            raise ParameterError
        savedir = unicodedata.normalize("NFC", savedir)
        savedir = re.sub(r'[\\/¥:*?"<>|]', "_", savedir)
        savedir = re.sub(r"\s+", " ", savedir.replace("\u3000", "")).strip()

    namefield = form.get("namefield")
    if namefield is not None and not isinstance(namefield, str):
        raise ParameterError
    if isinstance(namefield, str) and namefield.strip() == "":
        namefield = None

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

    # 認証情報・cookie 関連オプションは受け付けない(auth_profile を使う)
    try:
        cookies.check_options(options)
    except ValueError as e:
        raise ParameterError(str(e)) from None

    auth_profile = form.get("auth_profile")
    if auth_profile == "":
        auth_profile = None
    if auth_profile is not None:
        try:
            auth_profile = cookies.validate_profile(auth_profile)
        except ValueError as e:
            raise ParameterError(str(e)) from None

    return url, options, savedir, namefield, auth_profile

def probe_jobs(
        url: str,
        options: list,
        savedir: str | None,
        namefield: str | None,
        auth_profile: str | None = None) -> list[dict]:
    try:
        return function.probe_and_build_jobs(
            url, options, savedir, namefield, auth_profile)
    except RuntimeError as e:
        msg = str(e)
        print("ERROR: probe failed:", msg)
        raise


def add_request(
        url: str,
        options: list,
        savedir: str | None,
        namefield: str | None,
        auth_profile: str | None = None) -> None:
    key = f"{REQUESTS_PREFIX_BASE}"
    try:
        payload = json.dumps({
            "url": url,
            "options": options,
            "savedir": savedir,
            "namefield": namefield,
            "auth_profile": auth_profile,
            },
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

def login_required_response(
        auth_profile: str | None, reason: str) -> tuple[dict, int]:
    """ログインが必要なことをユーザへ通知する 401 応答を組み立てる。

    reason: cookie_missing(auth_profile 未指定) / profile_unknown(未登録)
            / cookie_expired(失効)
    """
    messages = {
        "cookie_missing": (
            "ログインが必要なコンテンツです。cookie でログインしたプロファイルを "
            "auth_profile に指定してください。"),
        "profile_unknown": (
            f"auth_profile '{auth_profile}' の cookie が登録されていません。"
            "ログインして cookie を登録してください。"),
        "cookie_expired": (
            f"auth_profile '{auth_profile}' の cookie が無効です。"
            "再ログインして cookie を更新してください。"),
    }
    body = {
        "error": "login_required",
        "reason": reason,
        "auth_profile": auth_profile,
        "message": messages[reason],
        "login_url": cookies.login_url(auth_profile),
    }
    print("WARNING: login required:", reason, "profile:", auth_profile)
    return jsonify(body), 401


class ProfileUnavailableError(cookies.LoginRequiredError):
    """指定プロファイルが未登録/失効中で、probe するまでもなくログインが必要。"""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def require_valid_profile(auth_profile: str | None) -> None:
    if not auth_profile:
        return
    state = cookies.profile_state(redis_client, auth_profile)
    if state != "valid":
        reason = "profile_unknown" if state == "missing" else "cookie_expired"
        raise ProfileUnavailableError(reason)


def login_required_from(
        error: cookies.LoginRequiredError,
        auth_profile: str | None) -> tuple[dict, int]:
    if isinstance(error, ProfileUnavailableError):
        return login_required_response(auth_profile, error.reason)
    # probe 由来: 指定 cookie が効かなかった(失効)か、そもそも未指定
    if auth_profile:
        cookies.mark_expired(redis_client, auth_profile)
        return login_required_response(auth_profile, "cookie_expired")
    return login_required_response(None, "cookie_missing")


def handle_download(
        url: str,
        options: list,
        savedir: str | None,
        namefield: str | None,
        auth_profile: str | None = None) -> tuple[dict, int]:
    jobs: list[dict] = []
    try:
        require_valid_profile(auth_profile)
        jobs = probe_jobs(url, options, savedir, namefield, auth_profile)
    except cookies.LoginRequiredError as e:
        # ログイン要求はサーバ不調ではないため、プロセス再起動は行わない
        return login_required_from(e, auth_profile)
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
    except ValueError as e:
        print("ERROR: invalid namefield:", e)
        return jsonify({"message": str(e)}), 400
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
        url, options, savedir, namefield, auth_profile = parse_request(form)
    except ParameterError as e:
        print("Error: Invalid request requested:", e.message)
        return jsonify({"message": e.message}), 400
    if DEBUG_MODE:
        print(
            "DEBUG REQUEST: "
            f"url={url}, options={options}, savedir={savedir}, "
            f"namefield={namefield}, auth_profile={auth_profile}")

    return handle_download(url, options, savedir, namefield, auth_profile)

@app.route("/schedule", methods=["POST"])
def schedule_endpoint() -> tuple[dict, int]:
    form = request.json
    try:
        url, options, savedir, namefield, auth_profile = parse_request(form)
    except ParameterError as e:
        print("Error: Invalid scheduled request:", e.message)
        return jsonify({"message": e.message}), 400

    try:
        add_request(url, options, savedir, namefield, auth_profile)
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
            namefield = req.get("namefield")
            auth_profile = req.get("auth_profile")

            msg, code = handle_download(
                url, options, savedir, namefield, auth_profile)
            if code == 401:
                # ログイン待ち: 予約を先頭へ戻し、ログイン要求をそのまま返す
                redis_client.lpush(key, entry)
                return msg, code
            if code != 200:
                raise Exception(msg.get_json().get("message", "Unknown error"))

            processed += 1
    except Exception as e:
        print("ERROR: failed processing scheduled requests:", e)
        return jsonify({"message": "Internal server error."}), 500

    return jsonify({
        "message": "Processed scheduled requests.", "count": str(processed)}), 200


@app.route("/auth/profiles", methods=["GET"])
def get_auth_profiles() -> tuple[dict, int]:
    # プロファイル名と状態のみ返す。cookie の中身は返さない
    return jsonify(cookies.list_profiles(redis_client)), 200


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
