# https://github.com/yt-dlp/yt-dlp/blob/master/yt_dlp/YoutubeDL.py
import os
import re
import shutil
import subprocess
import time
import unicodedata
from pathlib import Path
from typing import Any

TMP_DIR = Path("/tmpdownload")
SAVEDIR = Path("/download")
COPY_TIMEOUT = int(os.environ.get("COPY_TIMEOUT", "120"))
VIDEO_EXTS = {"avi", "flv", "mkv", "mov", "mp4", "webm"}

def run_yt_dlp(job: dict[str, Any]) -> tuple[bool, str]:
    url = job.get("url")
    options = job.get("options") or []
    savedir = job.get("savedir") or ""
    subpath = Path(unicodedata.normalize("NFC", savedir))
    Path.mkdir(subpath, exist_ok=True)

    provided_filename = job.get("filename")
    safe_name = None
    if isinstance(provided_filename, str) and provided_filename.strip():
        safe_name = Path(unicodedata.normalize("NFC", provided_filename)).name
        safe_name = re.sub(r'[\\/¥:*?"<>|]',"_", safe_name)

    if isinstance(safe_name, str) and safe_name.strip():
        outtmpl = str(TMP_DIR / subpath / (safe_name + ".%(ext)s"))
    else:
        outtmpl = str(SAVEDIR / subpath / "%(title)s.%(ext)s")

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

    if not isinstance(safe_name, str):
        return True, proc.stdout

    pattern = f"*{safe_name}*"
    dlpath: Path = None
    tmpfiledir = TMP_DIR / subpath
    mvresult = False
    for _ in range(COPY_TIMEOUT):
        time.sleep(1)
        if dlpath is None:
            for p in Path(tmpfiledir).rglob(pattern):
                if not p.is_file():
                    continue
                if safe_name not in p.name:
                    continue
                suffixes = [s.lower().lstrip(".") for s in p.suffixes]
                if not suffixes:
                    continue
                if not any(s in VIDEO_EXTS for s in suffixes):
                    continue
                dlpath = p
                print("INFO: found downloaded file:", dlpath)
                break
        if dlpath is None:
            continue

        dest = SAVEDIR / subpath / dlpath.name
        if dlpath.stat().st_size == 0:
            continue

        if dest.exists() and dest.stat().st_size > 0:
            print("INFO: destination exists, skipping copy:", dest)
            mvresult = True
            break
        try:
            shutil.copy2(dlpath, dest)
            print("INFO: copied from tmp:", p, "->", dest)
            mvresult = True
        except Exception as e:
            print("ERROR: failed to move from tmp:", dlpath, e)

    return True and mvresult, proc.stdout
