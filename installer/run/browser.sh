#!/bin/sh
set -eu
. "$(dirname -- "$0")/common.sh"
export TZ="$BROWSER_TZ" BROWSER_LANG COOKIE_DIR PORT="$BROWSER_PORT" SESSION_TIMEOUT
cd "$DIR/browserServer/src"
exec "$DIR/venv/bin/python3" -u main.py
