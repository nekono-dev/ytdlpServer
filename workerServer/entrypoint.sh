#!/bin/sh
# 既定 (引数無し) は dispatcher を起動する。
# dispatcher が worker コンテナを作るときは、command で `python3 -u /workspace/main.py`
# を明示的に渡す (この場合はそちらを実行する)。
# yt-dlp の更新は dispatcher が行う。ここでは pip install は行わない。
if [ "$#" -gt 0 ]; then
	exec "$@"
fi
exec python3 -u /workspace/dispatcher.py
