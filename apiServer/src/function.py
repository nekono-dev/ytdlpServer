from __future__ import annotations

import json
import subprocess


def probe_and_build_jobs(url: str, options: list, savedir: str | None) -> list[dict]:
    """Probe the `url` with yt_dlp and return a list of job records suitable for Redis.

    The function does NOT push to Redis; it only returns job dicts of the form:
    {"url": <target_url>, "options": <options>, "savedir": <savedir>}.
    """
    # For probing we call the yt-dlp CLI directly; pass `options` as-is.
    filtered_opts = options or []

    cmd = ["yt-dlp", "-j", "--no-progress", "--flat-playlist", *filtered_opts, url]

    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, check=True)
        out = proc.stdout or ""
    except subprocess.CalledProcessError as e:
        msg = f"yt-dlp probe failed (rc={e.returncode}): {
            e.stderr or e.stdout or str(e)}"
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

    # If we have multiple parsed objects, treat each as an entry (playlist)
    if len(objs) > 1:
        for entry in objs:
            if not isinstance(entry, dict):
                continue
            target_url = entry.get("webpage_url") or entry.get("url")
            entry_id = entry.get("id")
            extractor = entry.get("ie_key") or entry.get("extractor")

            job: dict = {"options": options, "savedir": savedir}
            job["filename"] = entry.get("title")
            if isinstance(target_url, str) and target_url.strip():
                job["url"] = target_url
            else:
                if entry_id is not None:
                    job["id"] = entry_id
                if extractor:
                    job["extractor"] = extractor
                job["source"] = url

            jobs.append(job)
    else:
        info = objs[0] if objs else None
        target = None
        if isinstance(info, dict):
            target = info.get("webpage_url") or info.get("url")
            # include filename when available
            filename = info.get("title")
            jobs.append({
                "url": target or url,
                "options": options, "savedir": savedir, "filename": filename})
            return jobs
        if not target:
            target = url
        jobs.append({"url": target, "options": options, "savedir": savedir})

    return jobs
