#!/bin/sh
# yt-dlp の更新は dispatcher (workerServer) が一括で行う (specs/apiServer/design.md)。
# ここでは起動するだけで、更新・定期再起動 (旧 SERVER_TTL) は行わない。
exec python3 -u /workspace/main.py
