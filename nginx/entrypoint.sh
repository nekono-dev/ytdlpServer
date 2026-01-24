#!/bin/sh
set -eu

# Generate new self-signed certs into /etc/nginx/certs on every container start
OUTDIR="/etc/nginx/certs"
mkdir -p "$OUTDIR"

CN=${SSL_CN:-localhost}
KEY="$OUTDIR/server.key"
CRT="$OUTDIR/server.crt"

echo "INFO: Generating self-signed certificate for CN=$CN"
openssl req -x509 -nodes -days 3650 -newkey rsa:2048 \
  -keyout "$KEY" -out "$CRT" \
  -subj "/C=US/ST=State/L=City/O=SelfSigned/CN=$CN" >/dev/null 2>&1 || true

echo "INFO: Certificate generated at $CRT"

# Start nginx in foreground
exec nginx -g 'daemon off;'
