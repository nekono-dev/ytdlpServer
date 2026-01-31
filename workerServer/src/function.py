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
SAVEDIR = Path(os.environ.get("DOWNLOAD_DIR", "/download"))
COPY_TIMEOUT = int(os.environ.get("COPY_TIMEOUT", "120"))
VIDEO_EXTS = {"avi", "flv", "mkv", "mov", "mp4", "webm"}
AUDIO_EXTS = {"aac", "alac", "flac", "m4a", "mp3", "opus", "vorbis", "wav"}

MAX_NAME_BYTES = 255

def run_yt_dlp(job: dict[str, Any]) -> tuple[bool, str]:
    url = job.get("url")
    options = job.get("options") or []
    savedir = job.get("savedir") or ""
    subpath = Path(unicodedata.normalize("NFC", savedir))
    Path.mkdir(subpath, exist_ok=True)

    safe_name = job.get("filename")
    if isinstance(safe_name, list):
        safe_name = "".join(str(x) for x in safe_name)
    # Ensure we never pass None into unicodedata.normalize
    safe_name = str(safe_name or "")
    safe_name = unicodedata.normalize("NFC", safe_name)
    safe_name = Path(safe_name).name
    safe_name = re.sub(r'[\\/¥:*?"<>|]', "_", safe_name)
    safe_name = re.sub(r"\s+", " ", safe_name.replace("\u3000", " ")).strip()

    print("INFO: Filename: ", safe_name)

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

    time.sleep(1)
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

        ## Safenameが有効である場合は safename + 拡張子
        ## そうでない場合はそのままコピー
        if safe_name is not None and safe_name != "":
            # safe_name を文字列化し、ファイル名長制限(バイト単位)に収める
            safe_base = str(safe_name)

            suffix_bytes = len(dlpath.suffix.encode("utf-8"))
            max_base_bytes = MAX_NAME_BYTES - suffix_bytes
            # 省略記号を付与するためのバイト長を考慮して処理する
            ellipsis = "..."

            # 元の文字列を保持して、実際に切り詰めが発生したかを判定する
            original = safe_base
            # safe_base + ellipsis が max_base_bytes に収まるように末尾を削る
            while len((safe_base + ellipsis).encode("utf-8")) > max_base_bytes and safe_base:
                safe_base = safe_base[:-1]

            # 切り詰めが発生していれば省略記号を付与する
            if safe_base:
                if len(original.encode("utf-8")) > len(safe_base.encode("utf-8")):
                    safe_base = safe_base + ellipsis
            else:
                # 何も残らなかった場合はフォールバック
                safe_base = "file"

            dest = SAVEDIR / subpath / (safe_base + dlpath.suffix)
        else:
            dest = SAVEDIR / subpath / dlpath.name

        if dlpath.stat().st_size == 0:
            continue

        try:
            Path.mkdir(SAVEDIR / subpath , exist_ok=True)
            shutil.copy2(dlpath, dest)
            print("INFO: copied from tmp:", p, "->", dest)
            mvresult = True
            break
        except Exception as e:
            print("ERROR: failed to move from tmp:", dlpath, e)

    return True and mvresult, proc.stdout
