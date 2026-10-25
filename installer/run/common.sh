#!/bin/sh
DIR=$HS_DIR
# shellcheck source=/dev/null
. "$HS_CONF_FILE"
REDIS_URL='redis://127.0.0.1:6379'
PATH="$DIR/ytdlp/current:$DIR/bin:$PATH"
if [ -z "$BROWSER_UI_URL" ]; then
  _ip=$(ip -4 route get 1.1.1.1 2>/dev/null | sed -n 's/.* src \([0-9.]*\).*/\1/p')
  [ -z "$_ip" ] || BROWSER_UI_URL="http://$_ip:$BROWSER_PORT"
fi
export DIR REDIS_URL PATH BROWSER_UI_URL
