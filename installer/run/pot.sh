#!/bin/sh
set -eu
. "$(dirname -- "$0")/common.sh"
cd "$DIR/pot-provider/server"
exec node build/main.js --host 127.0.0.1 --port "$POT_PORT"
