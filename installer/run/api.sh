#!/bin/sh
set -eu
. "$(dirname -- "$0")/common.sh"
export PORT="$API_PORT" COOKIE_DIR YTDLP_DIR="$DIR/ytdlp"
cd "$DIR/apiServer/src"
exec "$DIR/venv/bin/python3" -u main.py
