#!/bin/sh
set -eu
. "$(dirname -- "$0")/common.sh"
export NODE_ENV=production RI_SERVE_STATICS=true RI_BUILD_TYPE=DOCKER_ON_PREMISE
export RI_APP_FOLDER_ABSOLUTE_PATH="/var/lib/ytdlpserver/redisinsight" RI_APP_HOST="$REDIS_UI_HOST" RI_APP_PORT=5540
cd "$DIR/redisinsight"
exec node api/dist/src/main
