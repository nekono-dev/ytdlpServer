#!/bin/sh
# ytdlpServer を Alpine Linux (LXC / ベアメタル) へ Docker 無しでインストールする。
#
# 使い方 (root):
#   wget -qO- <URL>/install-alpine.sh | sh
#   WORKER_COUNT=4 DOWNLOAD_DIR=/mnt/video sh install-alpine.sh
#
# 何度実行しても同じ結果になる (冪等)。設定は環境変数で上書きでき、
# /etc/conf.d/ytdlpserver に保存される。再実行時は保存済みの値を引き継ぐ。
set -eu

# ---- 埋め込み値 (CI が書き換える) -------------------------------------------
REPO_URL_DEFAULT="https://github.com/nekono-dev/ytdlpServer"
REPO_REF_DEFAULT="main"
CF_VERSION_DEFAULT="2025.8.1"
CF_SHA256_AMD64_DEFAULT=""
CF_SHA256_ARM64_DEFAULT=""
# -----------------------------------------------------------------------------

CONF_FILE="/etc/conf.d/ytdlpserver"

usage() {
	cat <<'EOF'
使い方: install-alpine.sh [-h|--help]

環境変数 (既定値):
  REPO_URL          取得元リポジトリ
  REPO_REF          ブランチまたはタグ (埋め込み値)
  INSTALL_DIR       /opt/ytdlpserver
  DOWNLOAD_DIR      /mnt          動画の保存先
  TMP_DIR           /tmpdownload  一時ダウンロード先 (アプリ側で固定)
  WORKER_COUNT      1             worker の数
  API_PORT          5000
  POT_PORT          4416          PO Token プロバイダ (127.0.0.1 限定)
  SERVER_TTL        24            api の定期再起動間隔 (時間)
  REDIS_TTL         604800
  RETRY_COUNT       5

オプション (1 で有効):
  WITH_NGINX        1 で nginx (443, 自己署名証明書) を導入
  SSL_CN            localhost     自己署名証明書の CN
  WITH_CLOUDFLARED  1 で cloudflared を導入 (CLOUDFLARE_TOKEN が必須)
  CLOUDFLARE_TOKEN  Cloudflare Tunnel のトークン
  WITH_REDIS_INSIGHT 1 で redis-commander (Web UI, 5540) を導入
                    ※公式 Redis Insight は Alpine で動作しないため代替
  REDIS_UI_HOST     127.0.0.1     redis-commander の待ち受けアドレス
EOF
}

case "${1:-}" in
-h | --help)
	usage
	exit 0
	;;
esac

log() { echo "INFO: $*"; }
warn() { echo "WARNING: $*" >&2; }
die() {
	echo "ERROR: $*" >&2
	exit 1
}

[ "$(id -u)" = "0" ] || die "root で実行してください"
[ -f /etc/alpine-release ] || die "Alpine Linux 専用です"

# ---- 設定の読み込み (環境変数 > 保存済み > 既定値) ---------------------------
# 環境変数の指定を退避してから保存済みの設定を読み、指定があれば上書きする
_ENV_KEYS="REPO_URL REPO_REF INSTALL_DIR DOWNLOAD_DIR TMP_DIR WORKER_COUNT API_PORT POT_PORT \
SERVER_TTL REDIS_TTL RETRY_COUNT WITH_NGINX SSL_CN WITH_CLOUDFLARED CLOUDFLARE_TOKEN \
WITH_REDIS_INSIGHT REDIS_UI_HOST"
_saved=""
for _k in $_ENV_KEYS; do
	if eval "[ \"\${$_k+set}\" = set ]"; then
		eval "_v=\$$_k"
		# shellcheck disable=SC2154
		_saved="$_saved
$_k=$(printf '%s' "$_v" | sed "s/'/'\\\\''/g; s/^/'/; s/\$/'/")"
	fi
done
# shellcheck disable=SC1090
[ -f "$CONF_FILE" ] && . "$CONF_FILE"
[ -n "$_saved" ] && eval "$_saved"

REPO_URL="${REPO_URL:-$REPO_URL_DEFAULT}"
REPO_REF="${REPO_REF:-$REPO_REF_DEFAULT}"
INSTALL_DIR="${INSTALL_DIR:-/opt/ytdlpserver}"
DOWNLOAD_DIR="${DOWNLOAD_DIR:-/mnt}"
TMP_DIR="${TMP_DIR:-/tmpdownload}"
WORKER_COUNT="${WORKER_COUNT:-1}"
API_PORT="${API_PORT:-5000}"
POT_PORT="${POT_PORT:-4416}"
SERVER_TTL="${SERVER_TTL:-24}"
REDIS_TTL="${REDIS_TTL:-604800}"
RETRY_COUNT="${RETRY_COUNT:-5}"
WITH_NGINX="${WITH_NGINX:-0}"
SSL_CN="${SSL_CN:-localhost}"
WITH_CLOUDFLARED="${WITH_CLOUDFLARED:-0}"
CLOUDFLARE_TOKEN="${CLOUDFLARE_TOKEN:-}"
WITH_REDIS_INSIGHT="${WITH_REDIS_INSIGHT:-0}"
REDIS_UI_HOST="${REDIS_UI_HOST:-127.0.0.1}"

# 数値の検証
for _k in WORKER_COUNT API_PORT POT_PORT SERVER_TTL REDIS_TTL RETRY_COUNT; do
	eval "_v=\$$_k"
	case "$_v" in
	'' | *[!0-9]*) die "$_k は数値で指定してください: $_v" ;;
	esac
done
[ "$WORKER_COUNT" -ge 1 ] || die "WORKER_COUNT は 1 以上にしてください"
# アプリ側で /tmpdownload 固定のため変更は不可
[ "$TMP_DIR" = "/tmpdownload" ] || die "TMP_DIR はアプリ側で /tmpdownload に固定されています"
if [ "$WITH_CLOUDFLARED" = "1" ] && [ -z "$CLOUDFLARE_TOKEN" ]; then
	die "WITH_CLOUDFLARED=1 には CLOUDFLARE_TOKEN が必要です"
fi

SRC_DIR="$INSTALL_DIR/src"
VENV_DIR="$INSTALL_DIR/venv"
POT_DIR="$INSTALL_DIR/pot-provider"
BIN_DIR="$INSTALL_DIR/bin"

# ---- 設定ファイルの保存 -----------------------------------------------------
# 値はシングルクォートで囲み、sh から . で読み込める形にする
q() { printf "'%s'" "$(printf '%s' "$1" | sed "s/'/'\\\\''/g")"; }

write_conf() {
	mkdir -p "$(dirname "$CONF_FILE")"
	umask 077
	{
		echo "# ytdlpserver の設定 (install-alpine.sh が生成)。編集後は再起動すること。"
		for _k in REPO_URL REPO_REF INSTALL_DIR DOWNLOAD_DIR TMP_DIR WORKER_COUNT API_PORT POT_PORT \
			SERVER_TTL REDIS_TTL RETRY_COUNT WITH_NGINX SSL_CN WITH_CLOUDFLARED CLOUDFLARE_TOKEN \
			WITH_REDIS_INSIGHT REDIS_UI_HOST; do
			eval "_v=\$$_k"
			echo "$_k=$(q "$_v")"
		done
	} >"$CONF_FILE"
	chmod 600 "$CONF_FILE"
}

# ---- パッケージ -------------------------------------------------------------
install_packages() {
	log "パッケージをインストールします"
	apk update
	# pot-provider の canvas は musl 向けのビルド済みバイナリが無く、ソースからビルドされる
	apk add --no-cache \
		python3 py3-pip gcc g++ make pkgconf musl-dev python3-dev libffi-dev \
		cairo-dev pango-dev pixman-dev libjpeg-turbo-dev giflib-dev librsvg-dev \
		ffmpeg py3-mutagen nodejs npm git redis openrc ca-certificates wget
	_major="$(node -v | sed 's/^v//; s/\..*//')"
	[ "$_major" -ge 22 ] || die "pot-provider には Node.js 22 以上が必要です (現在: $(node -v))"
}

# ---- ソース -----------------------------------------------------------------
fetch_source() {
	log "ソースを取得します: $REPO_URL ($REPO_REF)"
	mkdir -p "$INSTALL_DIR"
	if [ -d "$SRC_DIR/.git" ]; then
		git -C "$SRC_DIR" remote set-url origin "$REPO_URL"
		git -C "$SRC_DIR" fetch --depth 1 origin "$REPO_REF"
		git -C "$SRC_DIR" checkout -q -f FETCH_HEAD
	else
		rm -rf "$SRC_DIR"
		git clone -q --depth 1 --branch "$REPO_REF" "$REPO_URL" "$SRC_DIR"
	fi
}

# ---- Python -----------------------------------------------------------------
setup_python() {
	log "Python 環境を構築します"
	# mutagen は apk 版を使うため system-site-packages を有効にする
	[ -x "$VENV_DIR/bin/python3" ] || python3 -m venv --system-site-packages "$VENV_DIR"
	"$VENV_DIR/bin/pip" install --no-cache-dir --upgrade \
		-r "$SRC_DIR/apiServer/requirements.txt" \
		-r "$SRC_DIR/workerServer/requirements.txt"

	# yt-dlp の設定。Docker のサービス名ではなくローカルの pot-provider を指す
	sed "s#http://pot-provider:4416#http://127.0.0.1:$POT_PORT#" \
		"$SRC_DIR/workerServer/yt-dlp.conf" >/etc/yt-dlp.conf
}

# ---- pot-provider -----------------------------------------------------------
setup_pot_provider() {
	log "pot-provider を構築します"
	_ver="$("$VENV_DIR/bin/pip" show bgutil-ytdlp-pot-provider | sed -n 's/^Version: //p')"
	[ -n "$_ver" ] || die "bgutil-ytdlp-pot-provider の版数を取得できません"
	# yt-dlp プラグインとサーバは同じ版数で揃える
	_url="https://github.com/Brainicism/bgutil-ytdlp-pot-provider"
	if [ -d "$POT_DIR/.git" ] && [ "$(git -C "$POT_DIR" describe --tags 2>/dev/null || true)" = "$_ver" ]; then
		log "pot-provider $_ver は取得済みです"
		# ビルド済みなら再ビルドしない (npm ci に時間がかかるため)
		[ -f "$POT_DIR/server/build/main.js" ] && return 0
	else
		rm -rf "$POT_DIR"
		git clone -q --depth 1 --branch "$_ver" "$_url" "$POT_DIR"
	fi
	(
		cd "$POT_DIR/server"
		npm ci
		npx tsc
	)
	[ -f "$POT_DIR/server/build/main.js" ] || die "pot-provider のビルドに失敗しました"
}

# ---- ディレクトリ -----------------------------------------------------------
setup_dirs() {
	mkdir -p "$DOWNLOAD_DIR" "$TMP_DIR"
	# 一時領域は tmpfs にする。LXC で mount できない場合は通常ディレクトリで継続
	if grep -q " $TMP_DIR tmpfs " /etc/fstab 2>/dev/null; then
		:
	else
		echo "tmpfs $TMP_DIR tmpfs defaults,nosuid,nodev 0 0" >>/etc/fstab
	fi
	if ! mountpoint -q "$TMP_DIR"; then
		mount "$TMP_DIR" 2>/dev/null ||
			warn "$TMP_DIR を tmpfs にできませんでした。通常のディレクトリを使用します (ディスクに一時ファイルが作られます)"
	fi
}

# ---- redis ------------------------------------------------------------------
setup_redis() {
	log "redis を設定します"
	# ローカルからのみ接続を許可する
	if grep -q '^bind ' /etc/redis.conf; then
		sed -i 's/^bind .*/bind 127.0.0.1/' /etc/redis.conf
	else
		echo "bind 127.0.0.1" >>/etc/redis.conf
	fi
	rc-update add redis default >/dev/null
}

# ---- OpenRC サービス --------------------------------------------------------
write_wrappers() {
	mkdir -p "$BIN_DIR"

	# 起動時に yt-dlp を最新化する (entrypoint.sh と同じ。失敗しても継続)
	cat >"$BIN_DIR/update-ytdlp" <<EOF
#!/bin/sh
"$VENV_DIR/bin/pip" install --upgrade --no-cache-dir "yt-dlp[default,curl-cffi]" >/dev/null 2>&1 || true
echo "yt-dlp \$("$VENV_DIR/bin/pip" show yt-dlp | grep Version || true)"
EOF

	cat >"$BIN_DIR/run-pot" <<EOF
#!/bin/sh
. "$CONF_FILE"
cd "$POT_DIR/server"
# 127.0.0.1 に限定する (認証が無いため外部へ公開しない)
exec node build/main.js --host 127.0.0.1 --port "\$POT_PORT"
EOF

	cat >"$BIN_DIR/run-api" <<EOF
#!/bin/sh
. "$CONF_FILE"
# アプリは yt-dlp を PATH から呼ぶため venv の bin を先頭に追加する
export PATH="$VENV_DIR/bin:\$PATH"
export REDIS_URL="redis://127.0.0.1:6379" SERVER_TTL PORT="\$API_PORT"
"$BIN_DIR/update-ytdlp"
cd "$SRC_DIR/apiServer/src"
# SERVER_TTL 時間で停止させ、supervise-daemon の respawn で再起動する
exec timeout "\$((SERVER_TTL * 3600))" "$VENV_DIR/bin/python3" -u main.py
EOF

	cat >"$BIN_DIR/run-worker" <<EOF
#!/bin/sh
. "$CONF_FILE"
export PATH="$VENV_DIR/bin:\$PATH"
export REDIS_URL="redis://127.0.0.1:6379" REDIS_TTL RETRY_COUNT DOWNLOAD_DIR
"$BIN_DIR/update-ytdlp"
cd "$TMP_DIR"
exec "$VENV_DIR/bin/python3" -u "$SRC_DIR/workerServer/src/main.py"
EOF
	chmod 755 "$BIN_DIR"/*
}

write_initd() {
	# $1=名前 $2=説明 $3=コマンド $4=依存 (need)
	cat >"/etc/init.d/$1" <<EOF
#!/sbin/openrc-run
description="$2"
supervisor="supervise-daemon"
command="$3"
output_log="/var/log/\${RC_SVCNAME}.log"
error_log="/var/log/\${RC_SVCNAME}.log"
respawn_delay=5
respawn_max=0

depend() {
	need net
	$4
}
EOF
	chmod 755 "/etc/init.d/$1"
}

setup_services() {
	log "OpenRC サービスを作成します"
	write_wrappers
	write_initd ytdlp-pot "ytdlpServer PO Token provider" "$BIN_DIR/run-pot" ""
	write_initd ytdlp-api "ytdlpServer API" "$BIN_DIR/run-api" "need redis
	after ytdlp-pot"
	write_initd ytdlp-worker "ytdlpServer worker" "$BIN_DIR/run-worker" "need redis
	after ytdlp-pot"

	# worker は ytdlp-worker.N のシンボリックリンクで複数台にする
	for _f in /etc/init.d/ytdlp-worker.*; do
		[ -L "$_f" ] || continue
		_n="${_f##*.}"
		if [ "$_n" -gt "$WORKER_COUNT" ]; then
			rc-service "ytdlp-worker.$_n" stop >/dev/null 2>&1 || true
			rc-update del "ytdlp-worker.$_n" default >/dev/null 2>&1 || true
			rm -f "$_f"
		fi
	done
	_i=1
	while [ "$_i" -le "$WORKER_COUNT" ]; do
		ln -sf ytdlp-worker "/etc/init.d/ytdlp-worker.$_i"
		_i=$((_i + 1))
	done
}

# 有効化して (再) 起動する
enable_and_restart() {
	for _s in "$@"; do
		rc-update add "$_s" default >/dev/null
		rc-service "$_s" restart || warn "$_s の起動に失敗しました (/var/log/$_s.log を確認)"
	done
}

# ---- オプション: nginx ------------------------------------------------------
setup_nginx() {
	log "nginx を設定します"
	apk add --no-cache nginx openssl
	mkdir -p /etc/nginx/certs /etc/nginx/http.d
	if [ ! -f /etc/nginx/certs/server.crt ]; then
		openssl req -x509 -nodes -days 3650 -newkey rsa:2048 \
			-keyout /etc/nginx/certs/server.key -out /etc/nginx/certs/server.crt \
			-subj "/C=US/ST=State/L=City/O=SelfSigned/CN=$SSL_CN" >/dev/null 2>&1
		chmod 600 /etc/nginx/certs/server.key
	fi
	# Alpine の nginx は http.d/*.conf を読み込む
	sed "s#http://api:5000#http://127.0.0.1:$API_PORT#" \
		"$SRC_DIR/nginx/conf.d/default.conf" >/etc/nginx/http.d/ytdlp.conf
	rm -f /etc/nginx/http.d/default.conf
	nginx -t
	enable_and_restart nginx
}

# ---- オプション: cloudflared ------------------------------------------------
setup_cloudflared() {
	log "cloudflared を設定します"
	case "$(uname -m)" in
	x86_64) _arch="amd64"; _sha="$CF_SHA256_AMD64_DEFAULT" ;;
	aarch64) _arch="arm64"; _sha="$CF_SHA256_ARM64_DEFAULT" ;;
	*) die "cloudflared 未対応のアーキテクチャです: $(uname -m)" ;;
	esac
	[ -n "$_sha" ] || die "cloudflared の SHA256 が埋め込まれていません (CI 経由の配布版を使用してください)"
	_tmp="$(mktemp)"
	wget -qO "$_tmp" \
		"https://github.com/cloudflare/cloudflared/releases/download/$CF_VERSION_DEFAULT/cloudflared-linux-$_arch"
	echo "$_sha  $_tmp" | sha256sum -c - >/dev/null || {
		rm -f "$_tmp"
		die "cloudflared の SHA256 が一致しません"
	}
	install -m 755 "$_tmp" /usr/local/bin/cloudflared
	rm -f "$_tmp"

	cat >"$BIN_DIR/run-cloudflared" <<EOF
#!/bin/sh
. "$CONF_FILE"
exec /usr/local/bin/cloudflared tunnel --no-autoupdate run --token "\$CLOUDFLARE_TOKEN"
EOF
	chmod 755 "$BIN_DIR/run-cloudflared"
	write_initd ytdlp-cloudflared "ytdlpServer Cloudflare Tunnel" "$BIN_DIR/run-cloudflared" "need ytdlp-api"
	enable_and_restart ytdlp-cloudflared
}

# ---- オプション: redis-commander (Redis Insight の代替) ---------------------
setup_redis_ui() {
	log "redis-commander を設定します"
	npm install -g --no-audit --no-fund redis-commander
	cat >"$BIN_DIR/run-redis-ui" <<EOF
#!/bin/sh
. "$CONF_FILE"
exec redis-commander --redis-host 127.0.0.1 --address "\$REDIS_UI_HOST" --port 5540
EOF
	chmod 755 "$BIN_DIR/run-redis-ui"
	write_initd ytdlp-redis-ui "redis-commander (Redis Web UI)" "$BIN_DIR/run-redis-ui" "need redis"
	enable_and_restart ytdlp-redis-ui
}

# ---- 実行 -------------------------------------------------------------------
write_conf
install_packages
fetch_source
setup_python
setup_pot_provider
setup_dirs
setup_redis
setup_services

# 起動順: redis → pot-provider → api / worker
enable_and_restart redis ytdlp-pot ytdlp-api
_i=1
while [ "$_i" -le "$WORKER_COUNT" ]; do
	enable_and_restart "ytdlp-worker.$_i"
	_i=$((_i + 1))
done

[ "$WITH_NGINX" = "1" ] && setup_nginx
[ "$WITH_CLOUDFLARED" = "1" ] && setup_cloudflared
[ "$WITH_REDIS_INSIGHT" = "1" ] && setup_redis_ui

log "インストールが完了しました"
rc-status -a 2>/dev/null | grep -E 'redis|ytdlp|nginx' || true
_ip="$(ip -4 route get 1.1.1.1 2>/dev/null | sed -n 's/.* src \([0-9.]*\).*/\1/p')"
echo "API:      http://${_ip:-<IPアドレス>}:$API_PORT/download"
[ "$WITH_NGINX" = "1" ] && echo "HTTPS:    https://${_ip:-<IPアドレス>}/download"
[ "$WITH_REDIS_INSIGHT" = "1" ] && echo "Redis UI: http://$REDIS_UI_HOST:5540"
echo "設定:     $CONF_FILE (編集後は rc-service で再起動)"
echo "ログ:     /var/log/ytdlp-*.log"
exit 0
