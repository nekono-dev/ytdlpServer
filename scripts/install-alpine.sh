#!/bin/sh
# ytdlpServer を Alpine Linux (LXC / ベアメタル) へ Docker 無しでインストールする。
#
# 使い方 (root):
#   wget -qO- <URL>/install-alpine.sh | sh
#   WORKER_MAX=4 DOWNLOAD_DIR=/mnt/video sh install-alpine.sh
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
RI_VERSION_DEFAULT="3.8.0"
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
  COOKIE_DIR        $INSTALL_DIR/cookies  cookie プロファイルの保存先 (ログインセッション)
  WORKER_MAX        1             同時に動く worker の最大数 (旧 WORKER_COUNT を別名として引き継ぐ)
  API_PORT          5000
  POT_PORT          4416          PO Token プロバイダ (127.0.0.1 限定)
  REDIS_TTL         604800
  RETRY_COUNT       5
  YTDLP_REPO        yt-dlp/yt-dlp 取得元 (GitHub Releases)
  UPDATE_INTERVAL   21600         yt-dlp の新版の確認間隔 (秒)
  UPDATE_RETRY_INTERVAL 1800      確認・適用に失敗した後の再確認の間隔 (秒)
  UPDATE_COOLDOWN   1800          probe 失敗による確認依頼の最短間隔 (秒)
  KEEP_VERSIONS     2             導入先に残す yt-dlp の版数
  DISPATCH_SCAN_INTERVAL 30       dispatcher がジョブを定期確認する間隔 (秒)
  LEASE_TTL         60            worker の生存確認 (リース) の有効期間 (秒)
  HEARTBEAT_INTERVAL 10           worker がリースを更新する間隔 (秒)
  STOP_GRACE        20            停止指示から yt-dlp を強制終了するまでの猶予 (秒)
  INPROGRESS_STALE  21600         所有者不明の in_progress を回収するまでの時間 (秒)

オプション (1 で有効):
  WITH_NGINX        1 で nginx (443, 自己署名証明書) を導入
  SSL_CN            localhost     自己署名証明書の CN
  WITH_CLOUDFLARED  1 で cloudflared を導入 (CLOUDFLARE_TOKEN が必須)
  CLOUDFLARE_TOKEN  Cloudflare Tunnel のトークン
  WITH_REDIS_INSIGHT 1 で Redis の Web UI (5540) を導入
  REDIS_UI          insight       insight (Redis Insight) または commander (redis-commander)
                    insight は公式ソースを取得して現地でビルドする (SSPL のため
                    ビルド済みバイナリは配布しない)。要 メモリ 2GB / Node.js 24 以上 /
                    所要 約 6 分。満たせない・失敗した場合は commander で継続する
  REDIS_INSIGHT_VERSION 3.8.0     ビルドする Redis Insight のタグ
  RI_BUILD_STORAGE  auto          Redis Insight のビルド先 (tmpfs | disk | auto)
                    tmpfs: 作業領域とキャッシュをメモリ上に置く。ディスクを使わないが
                           インストール時のみ メモリ 12GB 程度が必要 (終了後に解放)
                    disk:  ディスクを使う。空き 10GB 程度が必要 (ビルド後に削除)
                    auto:  メモリが足りれば tmpfs、足りない・マウントできなければ disk
  REDIS_UI_HOST     0.0.0.0       Web UI の待ち受けアドレス
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
_ENV_KEYS="REPO_URL REPO_REF INSTALL_DIR DOWNLOAD_DIR COOKIE_DIR WORKER_COUNT WORKER_MAX API_PORT POT_PORT \
REDIS_TTL RETRY_COUNT YTDLP_REPO UPDATE_INTERVAL UPDATE_RETRY_INTERVAL UPDATE_COOLDOWN \
KEEP_VERSIONS DISPATCH_SCAN_INTERVAL LEASE_TTL HEARTBEAT_INTERVAL STOP_GRACE INPROGRESS_STALE \
WITH_NGINX SSL_CN WITH_CLOUDFLARED CLOUDFLARE_TOKEN \
WITH_REDIS_INSIGHT REDIS_UI REDIS_INSIGHT_VERSION RI_BUILD_STORAGE REDIS_UI_HOST"
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
COOKIE_DIR="${COOKIE_DIR:-$INSTALL_DIR/cookies}"
# WORKER_COUNT は旧設定 (~v1.2 以前) の名残。WORKER_MAX が未指定なら、
# 保存済みの WORKER_COUNT (あれば) を引き継ぐ。新規インストールは WORKER_MAX を使う。
WORKER_MAX="${WORKER_MAX:-${WORKER_COUNT:-1}}"
unset WORKER_COUNT
API_PORT="${API_PORT:-5000}"
POT_PORT="${POT_PORT:-4416}"
REDIS_TTL="${REDIS_TTL:-604800}"
RETRY_COUNT="${RETRY_COUNT:-5}"
YTDLP_REPO="${YTDLP_REPO:-yt-dlp/yt-dlp}"
UPDATE_INTERVAL="${UPDATE_INTERVAL:-21600}"
UPDATE_RETRY_INTERVAL="${UPDATE_RETRY_INTERVAL:-1800}"
UPDATE_COOLDOWN="${UPDATE_COOLDOWN:-1800}"
KEEP_VERSIONS="${KEEP_VERSIONS:-2}"
DISPATCH_SCAN_INTERVAL="${DISPATCH_SCAN_INTERVAL:-30}"
LEASE_TTL="${LEASE_TTL:-60}"
HEARTBEAT_INTERVAL="${HEARTBEAT_INTERVAL:-10}"
STOP_GRACE="${STOP_GRACE:-20}"
INPROGRESS_STALE="${INPROGRESS_STALE:-21600}"
WITH_NGINX="${WITH_NGINX:-0}"
SSL_CN="${SSL_CN:-localhost}"
WITH_CLOUDFLARED="${WITH_CLOUDFLARED:-0}"
CLOUDFLARE_TOKEN="${CLOUDFLARE_TOKEN:-}"
WITH_REDIS_INSIGHT="${WITH_REDIS_INSIGHT:-1}"
REDIS_UI="${REDIS_UI:-insight}"
REDIS_INSIGHT_VERSION="${REDIS_INSIGHT_VERSION:-$RI_VERSION_DEFAULT}"
RI_BUILD_STORAGE="${RI_BUILD_STORAGE:-auto}"
REDIS_UI_HOST="${REDIS_UI_HOST:-0.0.0.0}"

# 数値の検証
for _k in WORKER_MAX API_PORT POT_PORT REDIS_TTL RETRY_COUNT UPDATE_INTERVAL \
	UPDATE_RETRY_INTERVAL UPDATE_COOLDOWN KEEP_VERSIONS DISPATCH_SCAN_INTERVAL \
	LEASE_TTL HEARTBEAT_INTERVAL STOP_GRACE INPROGRESS_STALE; do
	eval "_v=\$$_k"
	case "$_v" in
	'' | *[!0-9]*) die "$_k は数値で指定してください: $_v" ;;
	esac
done
[ "$WORKER_MAX" -ge 1 ] || die "WORKER_MAX は 1 以上にしてください"
case "$YTDLP_REPO" in
*/*) ;;
*) die "YTDLP_REPO は <所有者>/<リポジトリ> の形式で指定してください: $YTDLP_REPO" ;;
esac
case "$REDIS_UI" in
insight | commander) ;;
*) die "REDIS_UI は insight か commander を指定してください: $REDIS_UI" ;;
esac
case "$RI_BUILD_STORAGE" in
tmpfs | disk | auto) ;;
*) die "RI_BUILD_STORAGE は tmpfs / disk / auto のいずれかを指定してください: $RI_BUILD_STORAGE" ;;
esac
case "$REDIS_INSIGHT_VERSION" in
'' | *[!0-9A-Za-z._-]*) die "REDIS_INSIGHT_VERSION が不正です: $REDIS_INSIGHT_VERSION" ;;
esac
if [ "$WITH_CLOUDFLARED" = "1" ] && [ -z "$CLOUDFLARE_TOKEN" ]; then
	die "WITH_CLOUDFLARED=1 には CLOUDFLARE_TOKEN が必要です"
fi

SRC_DIR="$INSTALL_DIR/src"
VENV_DIR="$INSTALL_DIR/venv"
POT_DIR="$INSTALL_DIR/pot-provider"
BIN_DIR="$INSTALL_DIR/bin"
RI_DIR="$INSTALL_DIR/redisinsight"
RI_DATA_DIR="/var/lib/redisinsight"
YTDLP_DIR="$INSTALL_DIR/ytdlp"
PLUGIN_DIR="/etc/yt-dlp/plugins"

# ---- 設定ファイルの保存 -----------------------------------------------------
# 値はシングルクォートで囲み、sh から . で読み込める形にする
q() { printf "'%s'" "$(printf '%s' "$1" | sed "s/'/'\\\\''/g")"; }

write_conf() {
	mkdir -p "$(dirname "$CONF_FILE")"
	umask 077
	{
		echo "# ytdlpserver の設定 (install-alpine.sh が生成)。編集後は再起動すること。"
		for _k in REPO_URL REPO_REF INSTALL_DIR DOWNLOAD_DIR COOKIE_DIR WORKER_MAX API_PORT POT_PORT \
			REDIS_TTL RETRY_COUNT YTDLP_REPO UPDATE_INTERVAL UPDATE_RETRY_INTERVAL UPDATE_COOLDOWN \
			KEEP_VERSIONS DISPATCH_SCAN_INTERVAL LEASE_TTL HEARTBEAT_INTERVAL STOP_GRACE INPROGRESS_STALE \
			WITH_NGINX SSL_CN WITH_CLOUDFLARED CLOUDFLARE_TOKEN \
			WITH_REDIS_INSIGHT REDIS_UI REDIS_INSIGHT_VERSION RI_BUILD_STORAGE REDIS_UI_HOST; do
			eval "_v=\$$_k"
			echo "$_k=$(q "$_v")"
		done
	} >"$CONF_FILE"
	chmod 600 "$CONF_FILE"
}

# ---- パッケージ -------------------------------------------------------------
install_packages() {
	# メモリ不足だと npm ci / canvas のビルドが OOM kill やスラッシングで実質停止するため事前に検査する
	_mem_mb="$(awk '/^MemTotal:/ {print int($2 / 1024)}' /proc/meminfo)"
	if [ "$_mem_mb" -lt 1024 ]; then
		die "メモリが不足しています (${_mem_mb}MB)。1GB 以上を割り当ててください (Proxmox: pct set <CTID> -memory 1024)"
	fi
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

# ---- yt-dlp プラグイン (bgutil) -------------------------------------------
# バイナリ版 yt-dlp が読む標準の置き場へ、venv に入れた bgutil-ytdlp-pot-provider の
# yt_dlp_plugins を配置する (Docker イメージと同じ内容)。
setup_plugins() {
	log "yt-dlp プラグインを配置します"
	_site="$("$VENV_DIR/bin/python3" -c \
		"import sysconfig; print(sysconfig.get_paths()['purelib'])")"
	[ -d "$_site/yt_dlp_plugins" ] || die "bgutil-ytdlp-pot-provider のプラグインが見つかりません"
	mkdir -p "$PLUGIN_DIR/bgutil"
	rm -rf "$PLUGIN_DIR/bgutil/yt_dlp_plugins"
	cp -r "$_site/yt_dlp_plugins" "$PLUGIN_DIR/bgutil/"
	if ! grep -q '^--plugin-dirs ' /etc/yt-dlp.conf 2>/dev/null; then
		echo "--plugin-dirs $PLUGIN_DIR/bgutil" >>/etc/yt-dlp.conf
	fi
}

# ---- yt-dlp の初期導入 -------------------------------------------------------
# 導入先が空の場合のみ、GitHub Releases から初期の版を取得・検証する (冪等)。
# 失敗した場合はインストールを失敗させる (取得元に到達できない環境では使えない)。
setup_ytdlp() {
	log "yt-dlp を導入します (導入先: $YTDLP_DIR)"
	mkdir -p "$YTDLP_DIR"
	YTDLP_DIR="$YTDLP_DIR" YTDLP_REPO="$YTDLP_REPO" \
		"$VENV_DIR/bin/python3" "$SRC_DIR/workerServer/src/updater.py"
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
	mkdir -p "$DOWNLOAD_DIR"
	# cookie はパスワード同等のため root のみ参照可にする
	mkdir -p "$COOKIE_DIR"
	chmod 700 "$COOKIE_DIR"
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
	# 旧版 (~v1.2 以前) の名残。yt-dlp の更新は dispatcher が行うため不要になった
	rm -f "$BIN_DIR/update-ytdlp" "$BIN_DIR/run-worker"

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
# yt-dlp は導入先 (YTDLP_DIR/current) を PATH の先頭にして呼ぶ。
# 更新は dispatcher が行うため、api 自身は yt-dlp を更新せず、定期再起動もしない。
export PATH="$YTDLP_DIR/current:\$PATH"
export REDIS_URL="redis://127.0.0.1:6379" PORT="\$API_PORT" COOKIE_DIR YTDLP_DIR="$YTDLP_DIR"
cd "$SRC_DIR/apiServer/src"
exec "$VENV_DIR/bin/python3" -u main.py
EOF

	cat >"$BIN_DIR/run-dispatcher" <<EOF
#!/bin/sh
. "$CONF_FILE"
export PATH="$YTDLP_DIR/current:\$PATH"
export REDIS_URL="redis://127.0.0.1:6379" REDIS_TTL RETRY_COUNT DOWNLOAD_DIR COOKIE_DIR
export DISPATCH_MODE=process WORKER_MAX DISPATCH_SCAN_INTERVAL LEASE_TTL HEARTBEAT_INTERVAL \\
	STOP_GRACE INPROGRESS_STALE UPDATE_INTERVAL UPDATE_RETRY_INTERVAL UPDATE_COOLDOWN \\
	KEEP_VERSIONS YTDLP_REPO YTDLP_DIR="$YTDLP_DIR"
exec "$VENV_DIR/bin/python3" -u "$SRC_DIR/workerServer/src/dispatcher.py"
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
	# dispatcher: ジョブがあるときだけ worker (子プロセス) を起動する常駐サービス。
	# WORKER_MAX まで並列に起動する (worker 自体は OpenRC サービスとして登録しない)。
	write_initd ytdlp-dispatcher "ytdlpServer dispatcher" "$BIN_DIR/run-dispatcher" "need redis
	after ytdlp-pot"

	# 旧版 (~v1.2 以前) の worker サービス (複数台構成) を廃止する
	for _f in /etc/init.d/ytdlp-worker /etc/init.d/ytdlp-worker.*; do
		[ -e "$_f" ] || [ -L "$_f" ] || continue
		_name="$(basename "$_f")"
		rc-service "$_name" stop >/dev/null 2>&1 || true
		rc-update del "$_name" default >/dev/null 2>&1 || true
		rm -f "$_f"
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

# ---- オプション: redis-commander -------------------------------------------
setup_redis_commander() {
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

# ---- オプション: Redis Insight (公式ソースから現地ビルド) --------------------
# ビルド時の所要量 (MB。実測のピーク約 9.5GB に余裕を持たせた値)
RI_TMPFS_SIZE_MB=11000     # tmpfs の上限
RI_TMPFS_MIN_MEM_MB=12000  # tmpfs 利用に必要なメモリ (node のビルド処理分を含む)
RI_DISK_MIN_FREE_MB=10000  # disk 利用に必要な空き容量
# sass-embedded の glibc 向けバイナリのため、ビルド時のみ gcompat が必要 (実行時は不要)
# ビルドに成功したら 0、条件を満たさない・失敗した場合は 1 を返す (呼び出し側で commander にフォールバック)
build_redis_insight() {
	_ver="$REDIS_INSIGHT_VERSION"
	if [ -f "$RI_DIR/.version" ] && [ "$(cat "$RI_DIR/.version")" = "$_ver" ] &&
		[ -f "$RI_DIR/api/dist/src/main.js" ]; then
		log "Redis Insight $_ver はビルド済みです"
		return 0
	fi
	_major="$(node -v | sed 's/^v//; s/\..*//')"
	if [ "$_major" -lt 24 ]; then
		warn "Redis Insight には Node.js 24 以上が必要です (現在: $(node -v))。Alpine 3.23 以降を使用してください"
		return 1
	fi
	_mem_mb="$(awk '/^MemTotal:/ {print int($2 / 1024)}' /proc/meminfo)"
	if [ "$_mem_mb" -lt 1800 ]; then
		warn "Redis Insight のビルドにはメモリ 2GB 程度が必要です (${_mem_mb}MB)"
		return 1
	fi

	_work="$INSTALL_DIR/ri-build"
	mkdir -p "$INSTALL_DIR"
	# 前回の中断で tmpfs が残っていても再実行できるようにする
	umount "$_work" 2>/dev/null || true
	rm -rf "$_work"
	mkdir -p "$_work"

	# ビルドはピークで作業領域 約3.3GB + yarn キャッシュ 約5.7GB を使う。
	# tmpfs ならこれをメモリ上に置いてディスクを使わない (umount で全て解放される)
	_storage="$RI_BUILD_STORAGE"
	if [ "$_storage" != "disk" ]; then
		if [ "$_mem_mb" -lt "$RI_TMPFS_MIN_MEM_MB" ]; then
			if [ "$_storage" = "tmpfs" ]; then
				warn "tmpfs でのビルドにはメモリ ${RI_TMPFS_MIN_MEM_MB}MB 程度が必要です (${_mem_mb}MB)"
				return 1
			fi
			_storage="disk"
		elif mount -t tmpfs -o "size=${RI_TMPFS_SIZE_MB}m" tmpfs "$_work" 2>/dev/null; then
			_storage="tmpfs"
		elif [ "$_storage" = "tmpfs" ]; then
			warn "tmpfs をマウントできません (LXC の権限を確認してください)"
			return 1
		else
			warn "tmpfs をマウントできないためディスクでビルドします"
			_storage="disk"
		fi
	fi
	if [ "$_storage" = "disk" ]; then
		_free_mb="$(df -Pm "$_work" | awk 'NR==2 {print $4}')"
		if [ "$_free_mb" -lt "$RI_DISK_MIN_FREE_MB" ]; then
			warn "ビルドにはディスクの空き ${RI_DISK_MIN_FREE_MB}MB 程度が必要です (${_free_mb}MB)。RI_BUILD_STORAGE=tmpfs も選べます"
			rm -rf "$_work"
			return 1
		fi
	fi

	log "Redis Insight $_ver を公式ソースからビルドします (作業領域: $_storage、数分かかります)"
	if ! apk add --no-cache --virtual .ri-build python3 py3-setuptools make g++ git linux-headers gcompat >/dev/null; then
		[ "$_storage" = "tmpfs" ] && umount "$_work"
		rm -rf "$_work"
		return 1
	fi
	# set -e を効かせるため (if の条件内のサブシェルでは無効になる)、別プロセスで実行する
	cat >"$_work.sh" <<'BUILD_EOF'
set -eu
cd "$RI_WORK"
# キャッシュも作業領域に置く (tmpfs ならメモリ上、disk なら後で作業領域ごと削除される)
export XDG_CACHE_HOME="$RI_WORK/.cache" YARN_CACHE_FOLDER="$RI_WORK/.cache/yarn" npm_config_cache="$RI_WORK/.cache/npm"
wget -qO- "https://github.com/RedisInsight/RedisInsight/archive/refs/tags/$RI_VER.tar.gz" |
	tar xz --strip-components=1
if [ -f yarn.lock ]; then
	# 3.8.0 までは yarn 運用
	command -v yarn >/dev/null || npm install -g --no-audit --no-fund yarn
	SKIP_POSTINSTALL=1 yarn install
	yarn --cwd redisinsight/api install
	yarn build:ui
	yarn build:statics
	yarn build:api
	yarn --cwd redisinsight/api install --production
	cp redisinsight/api/.yarnclean.prod redisinsight/api/.yarnclean
	yarn --cwd redisinsight/api autoclean --force
else
	# 以降は npm 運用
	npm ci --ignore-scripts
	npx patch-package
	npm ci --prefix redisinsight/api
	npm run build:ui
	npm run build:statics
	npm run build:api
	npm ci --prefix redisinsight/api --omit=dev
fi
[ -f redisinsight/api/dist/src/main.js ] && [ -d redisinsight/ui/dist ]
# 実行に必要なものだけを配置する
rm -rf "$RI_DEST"
mkdir -p "$RI_DEST/api" "$RI_DEST/ui"
cp -r redisinsight/api/dist redisinsight/api/node_modules "$RI_DEST/api/"
cp -r redisinsight/ui/dist "$RI_DEST/ui/"
echo "$RI_VER" >"$RI_DEST/.version"
BUILD_EOF
	if RI_WORK="$_work" RI_VER="$_ver" RI_DEST="$RI_DIR" sh "$_work.sh"; then
		_rc=0
	else
		_rc=1
	fi
	# 成否に関わらずビルド用の一時物は片付ける (tmpfs は umount でメモリを解放する)
	[ "$_storage" = "tmpfs" ] && umount "$_work"
	rm -rf "$_work" "$_work.sh"
	apk del .ri-build >/dev/null 2>&1 || true
	[ "$_rc" = "0" ] || warn "Redis Insight のビルドに失敗しました"
	return "$_rc"
}

setup_redis_insight() {
	build_redis_insight || return 1
	mkdir -p "$RI_DATA_DIR"
	cat >"$BIN_DIR/run-redis-ui" <<EOF
#!/bin/sh
. "$CONF_FILE"
export NODE_ENV=production RI_SERVE_STATICS=true RI_BUILD_TYPE=DOCKER_ON_PREMISE
export RI_APP_FOLDER_ABSOLUTE_PATH="$RI_DATA_DIR" RI_APP_HOST="\$REDIS_UI_HOST" RI_APP_PORT=5540
cd "$RI_DIR"
exec node api/dist/src/main
EOF
	chmod 755 "$BIN_DIR/run-redis-ui"
	write_initd ytdlp-redis-ui "Redis Insight (Redis Web UI)" "$BIN_DIR/run-redis-ui" "need redis"
	enable_and_restart ytdlp-redis-ui
}

setup_redis_ui() {
	if [ "$REDIS_UI" = "insight" ]; then
		setup_redis_insight && return 0
		warn "Redis Insight を導入できないため redis-commander で継続します"
	fi
	setup_redis_commander
}

# ---- 実行 -------------------------------------------------------------------
write_conf
install_packages
fetch_source
setup_python
setup_plugins
setup_ytdlp
setup_pot_provider
setup_dirs
setup_redis
setup_services

# 起動順: redis → pot-provider → api / dispatcher (worker は dispatcher が起動する)
enable_and_restart redis ytdlp-pot ytdlp-api ytdlp-dispatcher

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
