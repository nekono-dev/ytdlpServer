#!/bin/sh
set -eu
. "$(dirname -- "$0")/common.sh"
exec redis-commander --redis-host 127.0.0.1 --address "$REDIS_UI_HOST" --port 5540
