#!/bin/sh
set -eu
. "$(dirname -- "$0")/common.sh"
export TUNNEL_TOKEN="$CLOUDFLARE_TOKEN"
exec /usr/local/bin/cloudflared tunnel --no-autoupdate run
