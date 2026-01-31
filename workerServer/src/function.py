# https://github.com/yt-dlp/yt-dlp/blob/master/yt_dlp/YoutubeDL.py
import os
import shutil
import subprocess
import time
import unicodedata
from pathlib import Path
from typing import Any

TMP_DIR = Path("/tmpdownload")
SAVEDIR = Path(os.environ.get("DOWNLOAD_DIR", "/download"))
COPY_TIMEOUT = int(os.environ.get("COPY_TIMEOUT", "120"))
VIDEO_EXTS = {"avi", "flv", "mkv", "mov", "mp4", "webm"}
AUDIO_EXTS = {"aac", "alac", "flac", "m4a", "mp3", "opus", "vorbis", "wav"}

def run_yt_dlp(job: dict[str, Any]) -> tuple[bool, str]:
    url = job.get("url")
    options = job.get("options") or []
    savedir = job.get("savedir") or ""
    subpath = Path(unicodedata.normalize("NFC", savedir))
    Path.mkdir(subpath, exist_ok=True)

    safe_name = job.get("filename")

    # Prefer using job id as base filename for download to ensure deterministic names
    job_id = job.get("id")
    outtmpl = str(TMP_DIR / subpath / (str(job_id) + ".%(ext)s"))

    cmd = ["yt-dlp", "--no-progress", *options, "-o", outtmpl, "--no-playlist", url]
    print("INFO: Running yt-dlp:", " ".join(cmd))

    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, check=True)
        print("INFO: yt-dlp succeeded for:", url)
    except subprocess.CalledProcessError as e:
        print("ERROR: yt-dlp failed (rc=", e.returncode, "):", e.stderr)
        return False, e.stderr or e.stdout or str(e)
    except FileNotFoundError:
        msg = "yt-dlp not found in PATH"
        print("ERROR:", msg)
        return False, msg
    except Exception as e:
        print("ERROR: Unexpected error running yt-dlp:", e)
        return False, str(e)

    pattern = f"*{job_id}*"
    dlpath: Path = None
    tmpfiledir = TMP_DIR / subpath
    mvresult = False
    for _ in range(COPY_TIMEOUT):
        time.sleep(1)
        if dlpath is None:
            for p in Path(tmpfiledir).rglob(pattern):
                if not p.is_file():
                    continue
                if str(job_id) not in p.name:
                        continue
                suffixes = [s.lower().lstrip(".") for s in p.suffixes]
                if not suffixes:
                    continue
                if not any(s in (VIDEO_EXTS | AUDIO_EXTS) for s in suffixes):
                    continue
                dlpath = p
                print("INFO: found downloaded file:", dlpath)
                break
        if dlpath is None:
            continue

        dest_name = None
        ## Safenameが有効である場合は safename + 拡張子
        ## そうでない場合はそのままコピー
        if isinstance(safe_name, str) and safe_name.strip():
            dest_name = safe_name + dlpath.suffix
            dest = SAVEDIR / subpath / dest_name
        else:
            dest = SAVEDIR / subpath / dlpath.name

        if dlpath.stat().st_size == 0:
            continue

        if dest.exists() and dest.stat().st_size > 0:
            print("INFO: destination exists, skipping copy:", dest)
            mvresult = True
            break
        try:
            Path.mkdir(SAVEDIR / subpath , exist_ok=True)
            shutil.copy2(dlpath, dest)
            print("INFO: copied from tmp:", p, "->", dest)
            mvresult = True
        except Exception as e:
            print("ERROR: failed to move from tmp:", dlpath, e)

    return True and mvresult, proc.stdout
