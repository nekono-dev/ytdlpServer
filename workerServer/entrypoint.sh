#!/bin/sh

## update yt-dlp before starting the server
pip3 install --upgrade --break-system-packages --root-user-action ignore yt-dlp[default,curl-cffi]

echo "INFO: yt-dlp updated to latest version."
## start the server (use exec so PID 1 is the python process and signals are forwarded)
exec python3 -u /workspace/main.py