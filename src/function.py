# https://github.com/yt-dlp/yt-dlp/blob/master/yt_dlp/YoutubeDL.py
import os
import unicodedata
from pathlib import Path
from urllib.parse import urlparse

from yt_dlp import YoutubeDL


# https://github.com/yt-dlp/yt-dlp/issues/387#issuecomment-1195182084
def add_dict_exargs(param: dict) -> dict:
    param["extractor_args"] = {
        "youtube": {
            "lang": [param["language"]],
        },
    }
    print("INFO: add extractor_args for youtube")
    return param


## Process
def download(url: str, param: dict, myparam: dict) -> None:
    site = urlparse(url)
    # youtubeの場合は言語指定を追加
    if "youtube" in site.hostname:
        param = add_dict_exargs(param=param)
    param["quiet"] = True
    param["noplaylist"] = True
    param["overwrites"] = True
    param["extractor_retries"] = 10
    param["retries"] = 10
    param["wait_for_video"] = [3, 60]
    param["windowsfilenames"] = True
    param["outtmpl"] = str(get_title(url=url, param=param) + ".%(ext)s")

    if not myparam["origts"]:
        param["progress_hooks"] = [update_ts]

    if myparam["category"]:
        ## Mac対応
        subpath = Path(unicodedata.normalize("NFC", str(myparam["category"])))
        Path.mkdir(subpath, exist_ok=True)
        filename = unicodedata.normalize("NFC", str(subpath.joinpath(param["outtmpl"])))
        param["outtmpl"] = filename

    with YoutubeDL(param) as ydl:
        ydl.download(url)


def update_ts(d: dict) -> None:
    if d["status"] == "finished":
        filename = d["filename"]
        os.utime(path=Path.cwd().joinpath(filename), times=None)


def get_title(url: str, param: dict) -> str:
    with YoutubeDL(param) as ydl:
        info_dict = ydl.extract_info(url, download=False)
        ydl.list_formats(info_dict)
        return (
            info_dict.get("title").replace("/", "-").replace("\\", "-")
            + " ["
            + info_dict["id"]
            + "]"
        )
