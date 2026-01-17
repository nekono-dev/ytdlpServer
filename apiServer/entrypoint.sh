#!/bin/sh

## update yt-dlp before starting the server
pip install --upgrade --break-system-packages --root-user-action ignore yt-dlp

echo "INFO: yt-dlp updated to latest version."
## start the server (use exec so PID 1 is the python process and signals are forwarded)
exec python3 -u /workspace/main.py