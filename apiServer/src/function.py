from __future__ import annotations

import json
import re
import subprocess
import unicodedata
import uuid
from pathlib import Path

import cookies

INVALID_FS_CHARS = re.compile(r'[\\/¥:*?"<>|]')
TEMPLATE_PATTERN = re.compile(r"%\(([^)]+)\)s")
MAX_JOB_ID_BYTES = 200

# 子供向け動画などは既定クライアントだけでは取得できず "この動画はご覧いただけません" となる。
# PO Token が必要な mweb クライアントを既定に追加して取得できるようにする。
YOUTUBE_PLAYER_CLIENT = "player_client=default,mweb"
YOUTUBE_ARGS_PATTERN = re.compile(r"^youtube:", re.IGNORECASE)


def with_youtube_defaults(options: list[str]) -> list[str]:
    """`--extractor-args youtube:...` に player_client の既定値を補って返す。

    yt-dlp は同じ抽出器への --extractor-args を後勝ちで上書きするため、
    設定ファイルではなく、ユーザ指定の youtube: 引数へ直接マージする。
    ユーザが player_client を明示している場合はそれを尊重する。
    """
    result = list(options)
    found = False
    for i, opt in enumerate(result):
        if opt == "--extractor-args" and i + 1 < len(result):
            index, prefix, value = i + 1, "", result[i + 1]
        elif opt.startswith("--extractor-args="):
            index, prefix, value = i, "--extractor-args=", opt.split("=", 1)[1]
        else:
            continue

        if not YOUTUBE_ARGS_PATTERN.match(value):
            continue

        found = True
        if "player_client=" not in value:
            result[index] = f"{prefix}{value.rstrip(';')};{YOUTUBE_PLAYER_CLIENT}"

    if not found:
        result += ["--extractor-args", f"youtube:{YOUTUBE_PLAYER_CLIENT}"]

    return result


def _sanitize_name(value: str) -> str:
    normalized = unicodedata.normalize("NFC", str(value))
    normalized = Path(normalized).name
    normalized = INVALID_FS_CHARS.sub("_", normalized)
    normalized = re.sub(r"\s+", " ", normalized.replace("\u3000", " ")).strip()
    return normalized


def _trim_utf8_bytes(value: str, max_bytes: int) -> str:
    trimmed = value
    while len(trimmed.encode("utf-8")) > max_bytes and trimmed:
        trimmed = trimmed[:-1]
    return trimmed


def _render_namefield(namefield: str, entry: dict, row_number: int) -> str:
    keys = TEMPLATE_PATTERN.findall(namefield)
    if not keys:
        return _sanitize_name(namefield)

    missing_keys = [k for k in keys if k not in entry or entry.get(k) is None]
    if missing_keys:
        unique_missing = sorted(set(missing_keys))
        raise ValueError(
            "Invalid namefield: missing keys "
            f"{', '.join(unique_missing)} at entry #{row_number}")

    rendered = namefield
    for key in keys:
        rendered = rendered.replace(f"%({key})s", str(entry.get(key)))

    return _sanitize_name(rendered)


def probe_and_build_jobs(
        url: str,
        options: list,
        savedir: str | None,
        namefield: str | None,
        auth_profile: str | None = None) -> list[dict]:
    """Probe the `url` with yt_dlp and return a list of job records suitable for Redis.

    The function does NOT push to Redis; it only returns job dicts of the form:
    {"url": <target_url>, "options": <options>, "savedir": <savedir>}.
    `auth_profile` を指定すると cookie を使って probe し、ジョブにも引き継ぐ。
    ログイン要求(cookie 未指定/失効)は cookies.LoginRequiredError を送出する。
    """
    filtered_opts = with_youtube_defaults(
        [opt for opt in (options or []) if opt != "--no-playlist"])

    with cookies.cookie_file(auth_profile) as cookie_path:
        cookie_opts = ["--cookies", cookie_path] if cookie_path else []
        cmd = [
            "yt-dlp", "-j", "--no-progress", "--flat-playlist",
            *cookie_opts, *filtered_opts, url]

        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, check=True)
            out = proc.stdout or ""
        except subprocess.CalledProcessError as e:
            detail = e.stderr or e.stdout or str(e)
            msg = f"yt-dlp probe failed (rc={e.returncode}): {detail}"
            if cookies.classify_login_required(detail):
                raise cookies.LoginRequiredError(msg) from e
            raise RuntimeError(msg) from e
        except FileNotFoundError:
            msg = "yt-dlp not found in PATH"
            raise RuntimeError(msg) from None

    objs: list = []

    try:
        for line in out.splitlines():
            linestriped = line.strip()
            if not linestriped:
                continue
            parsed = json.loads(linestriped)
            objs.append(parsed)
    except Exception:
        msg = "failed to parse yt-dlp JSON output"
        raise RuntimeError(msg) from None

    jobs: list[dict] = []
    generated_name_counts: dict[str, int] = {}

    for idx, entry in enumerate(objs, start=1):
        if not isinstance(entry, dict):
            continue

        target_url = entry.get("webpage_url") or entry.get("url")
        if target_url is None:
            print("WARN: No URL extracted. Use requested URL.")
            target_url = url

        entry_id = entry.get("id")
        fallback_id = str(entry_id) if entry_id is not None else uuid.uuid4().hex
        extractor = entry.get("ie_key") or entry.get("extractor")

        job: dict = {"options": options, "savedir": savedir}
        if auth_profile:
            job["auth_profile"] = auth_profile

        template_context = dict(entry)
        template_context["id"] = fallback_id

        filename_raw = entry.get("title") or fallback_id
        job_id_raw = fallback_id

        if isinstance(namefield, str) and namefield.strip() != "":
            rendered = _render_namefield(namefield, template_context, idx)
            if rendered == "":
                raise ValueError(f"Invalid namefield: rendered empty at entry #{idx}")

            basename = rendered
            current_count = generated_name_counts.get(basename, 0) + 1
            generated_name_counts[basename] = current_count

            if current_count > 1:
                rendered = f"{basename}-{current_count}"

            filename_raw = rendered
            job_id_raw = rendered

        filename_sanitized = _sanitize_name(str(filename_raw))
        if filename_sanitized == "":
            filename_sanitized = _sanitize_name(str(fallback_id))

        job_id_sanitized = _sanitize_name(str(job_id_raw))
        job_id_sanitized = _trim_utf8_bytes(job_id_sanitized, MAX_JOB_ID_BYTES)
        if job_id_sanitized == "":
            job_id_sanitized = fallback_id

        job["filename"] = filename_sanitized
        job["id"] = job_id_sanitized

        if isinstance(target_url, str) and target_url.strip():
            job["url"] = target_url
        else:
            if extractor:
                job["extractor"] = extractor
            job["source"] = url

        jobs.append(job)

    return jobs
