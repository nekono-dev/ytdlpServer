# https://github.com/yt-dlp/yt-dlp/blob/master/yt_dlp/YoutubeDL.py
import os
from pathlib import Path

from yt_dlp import YoutubeDL


## Process
def download(url: str, param: dict, myparam: dict) -> None:
    param["quiet"] = True
    param["noplaylist"] = True
    param["overwrites"] = True
    param["outtmpl"] = str(get_title(url=url) + ".%(ext)s")
    param["writethumbnail"] = "true"
    param["extractor_retries"] = 10
    param["retries"] = 10
    param["wait_for_video"] = [3, 60]
    param["postprocessors"] = [
        {
            "key": "EmbedThumbnail",
            "already_have_thumbnail": False,
        },
    ]
    if not myparam["origts"]:
        param["progress_hooks"] = [update_ts]

    with YoutubeDL(param) as ydl:
        ydl.download(url)


def update_ts(d: dict) -> None:
    if d["status"] == "finished":
        filename = d["filename"]
        os.utime(path=Path.cwd().joinpath(filename), times=None)


def get_title(url: str) -> str:
    with YoutubeDL({"noplaylist": True}) as ydl:
        info_dict = ydl.extract_info(url, download=False)
        ydl.list_formats(info_dict)
        return (
            info_dict.get("title").replace("/", "-").replace("\\", "-")
            + " ["
            + info_dict["id"]
            + "]"
        )
