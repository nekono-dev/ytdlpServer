#!/bin/sh

# Update yt-dlp before starting the server
pip3 install --upgrade --break-system-packages --root-user-action ignore yt-dlp[default,curl-cffi] > /dev/null 2>&1 || true
echo "yt-dlp $(pip3 show yt-dlp | grep Version || true)"

echo "INFO: yt-dlp updated to latest version."

# Start the server in background so this wrapper can schedule a restart.
python3 -u /workspace/main.py &
CHILD=$!

# Determine TTL from SERVER_TTL env (hours). Default to 24 if unset/invalid.
# Convert to seconds for sleep.
if [ -n "${SERVER_TTL:-}" ] && echo "$SERVER_TTL" | grep -Eq '^[0-9]+$'; then
	TTL_HOURS="$SERVER_TTL"
else
	TTL_HOURS=24
fi
TTL_SEC=$((TTL_HOURS * 3600))
echo "INFO: Using SERVER_TTL=${TTL_HOURS} hour(s) => ${TTL_SEC} seconds"

# Background timer: after TTL_SEC seconds, request termination of the child process
(
	sleep "$TTL_SEC"
	echo "INFO: Scheduled restart: killing process $CHILD"
	kill -TERM "$CHILD" 2>/dev/null || true
) &

# Forward INT/TERM to child and wait for it
handle_term() {
	echo "INFO: Signal received, forwarding to child $CHILD"
	kill -TERM "$CHILD" 2>/dev/null || true
	wait "$CHILD" || true
	exit 0
}

trap 'handle_term' INT TERM

wait "$CHILD"
exit $?