# https://github.com/yt-dlp/yt-dlp/blob/master/yt_dlp/YoutubeDL.py
import os
import re
import subprocess
import unicodedata
from pathlib import Path
from typing import Any

import cookies

SAVEDIR = Path(os.environ.get("DOWNLOAD_DIR", "/download"))
COPY_TIMEOUT = int(os.environ.get("COPY_TIMEOUT", "120"))
VIDEO_EXTS = {"avi", "flv", "mkv", "mov", "mp4", "webm"}
AUDIO_EXTS = {"aac", "alac", "flac", "m4a", "mp3", "opus", "vorbis", "wav"}

MAX_NAME_BYTES = 255

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

def run_yt_dlp(
        job: dict[str, Any],
        on_process_start: Any = None) -> tuple[bool, str]:
    """yt-dlp を実行する。

    `on_process_start` が渡されれば、起動した `subprocess.Popen` を渡して呼ぶ。
    停止の指示 (SIGTERM 等) を子プロセスへ転送するために、呼び出し側 (main.py) が
    そのプロセスへ signal を送れるようにする。
    """
    url = job.get("url")
    options = job.get("options") or []
    savedir = job.get("savedir") or ""
    subpath = Path(unicodedata.normalize("NFC", savedir))
    Path.mkdir(SAVEDIR / subpath, parents=True, exist_ok=True)

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

    job_id = job.get("id")

    # 直接配置方式: 最終保存先に yt-dlp が直接出力する
    # safe_name が空の場合は job_id を利用し、常に拡張子は yt-dlp に決定させる
    base_name = safe_name if safe_name else str(job_id)
    outtmpl = str(SAVEDIR / subpath / (base_name + ".%(ext)s"))

    try:
        # auth_profile 指定時は cookie の一時コピーを渡し、更新分はストアへ書き戻す
        with cookies.cookie_file(job.get("auth_profile")) as cookie_path:
            cookie_opts = ["--cookies", cookie_path] if cookie_path else []
            cmd = [
                "yt-dlp", "--no-progress", *cookie_opts,
                *with_youtube_defaults(options),
                "-o", outtmpl, "--no-playlist", url,
            ]
            print("INFO: Running yt-dlp:", " ".join(cookies.mask_command(cmd)))
            proc = subprocess.Popen(  # noqa: S603
                cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            if on_process_start is not None:
                on_process_start(proc)
            stdout, stderr = proc.communicate()
        if proc.returncode != 0:
            print("ERROR: yt-dlp failed (rc=", proc.returncode, "):", stderr)
            return False, stderr or stdout or f"yt-dlp exited with {proc.returncode}"
        print("INFO: yt-dlp succeeded for:", url)
    except cookies.ProfileNotFoundError as e:
        print("ERROR:", e)
        return False, str(e)
    except FileNotFoundError:
        msg = "yt-dlp not found in PATH"
        print("ERROR:", msg)
        return False, msg
    except OSError as e:
        print("ERROR: Unexpected error running yt-dlp:", e)
        return False, str(e)

    # 旧二段階方式（tmp -> copy）は無効化。実行成功をそのまま成功として返す。
    return True, stdout
