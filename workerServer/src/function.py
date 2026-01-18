# https://github.com/yt-dlp/yt-dlp/blob/master/yt_dlp/YoutubeDL.py
import os
import re
import shutil
import subprocess
import time
import unicodedata
from pathlib import Path
from typing import Any

TMP_DIR = Path("/tmp/ytdlp")
COPY_TIMEOUT = int(os.environ.get("COPY_TIMEOUT", "120"))

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
        outtmpl = str(subpath / "%(title)s.%(ext)s")

    cmd = ["yt-dlp", "--no-progress", *options, "-o", outtmpl, "--no-playlist", url]
    print("INFO: Running yt-dlp:", " ".join(cmd))

    result = False, ""
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, check=True)
        print("INFO: yt-dlp succeeded for:", url)

        if not isinstance(safe_name, str):
            result = True, proc.stdout

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
                    if p.suffix.lower() in {".part", ".ytdl"}:
                        continue
                    if safe_name in p.name:
                        dlpath = p
                        break
                if dlpath is None:
                    break
            dest = subpath / dlpath.name
            if dlpath.stat().st_size == 0:
                continue

            if dest.exists():
                print("INFO: destination exists, skipping copy:", dest)
                Path.unlink(p)
                mvresult = True
                break
            try:
                shutil.copy2(p, dest)
                print("INFO: copied from tmp:", p, "->", dest)
                Path.unlink(p)
                mvresult = True
            except Exception as e:
                print("ERROR: failed to move from tmp:", p, e)

        result = True and mvresult, proc.stdout
    except subprocess.CalledProcessError as e:
        print("ERROR: yt-dlp failed (rc=", e.returncode, "):", e.stderr)
        result = False, e.stderr or e.stdout or str(e)
    except FileNotFoundError:
        msg = "yt-dlp not found in PATH"
        print("ERROR:", msg)
        result = False, msg
    except Exception as e:
        print("ERROR: Unexpected error running yt-dlp:", e)
        result = False, str(e)

    return result
