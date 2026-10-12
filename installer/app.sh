#!/bin/sh
UNIT_DIR=/etc/systemd/system
RI_DATA_DIR="$DATA_DIR/redisinsight"
NGINX_CONF=/etc/nginx/conf.d/ytdlpserver.conf
PLUGIN_DIR=/etc/yt-dlp/plugins/bgutil
PLAYWRIGHT_DIR="$DIR/browsers"

warn() { printf '警告: %s\n' "$*" 1>&2; }
mem_mb() { awk '/^MemTotal:/ {print int($2 / 1024)}' /proc/meminfo; }
free_mb() { df -Pm "$1" | awk 'NR==2 {print $4}'; }
apt_install() { DEBIAN_FRONTEND=noninteractive apt-get install -y -qq --no-install-recommends "$@"; }
unit_exists() { [ -f "$UNIT_DIR/$1.service" ]; }

check_env() {
  [ "$(sed -n 's/^ID=//p' /etc/os-release | tr -d '"')" = ubuntu ] \
    && [ "$(sed -n 's/^VERSION_ID=//p' /etc/os-release | tr -d '"')" = 24.04 ] \
    || die "Ubuntu 24.04 専用です"
  [ -d /run/systemd/system ] || die "systemd が動作していません"
  case "$(uname -m)" in x86_64|aarch64) ;; *) die "未対応のアーキテクチャです: $(uname -m)" ;; esac
  [ "$(mem_mb)" -ge 1024 ] || die "メモリが不足しています（$(mem_mb)MB）。1024MB 以上を割り当ててください"
}

is_number() { case "$1" in ''|*[!0-9]*) return 1 ;; esac; }

check_values() {
  _v=''
  for _k in WORKER_MAX API_PORT POT_PORT REDIS_TTL RETRY_COUNT UPDATE_INTERVAL UPDATE_RETRY_INTERVAL \
    UPDATE_COOLDOWN KEEP_VERSIONS DISPATCH_SCAN_INTERVAL LEASE_TTL HEARTBEAT_INTERVAL STOP_GRACE \
    INPROGRESS_STALE BROWSER_PORT SESSION_TIMEOUT; do
    eval "_v=\$$_k"
    is_number "$_v" || die "$_k は数字で指定してください: $_v"
  done
  [ "$WORKER_MAX" -ge 1 ] || die "WORKER_MAX は 1 以上にしてください"
  case "$YTDLP_REPO" in */*) ;; *) die "YTDLP_REPO は 所有者/リポジトリ の形で指定してください: $YTDLP_REPO" ;; esac
  case "$REDIS_UI" in insight|commander) ;; *) die "REDIS_UI は insight か commander を指定してください: $REDIS_UI" ;; esac
  case "$RI_BUILD_STORAGE" in tmpfs|disk|auto) ;; *) die "RI_BUILD_STORAGE は tmpfs・disk・auto のいずれかです: $RI_BUILD_STORAGE" ;; esac
  case "$REDIS_INSIGHT_VERSION" in ''|*[!0-9A-Za-z._-]*) die "REDIS_INSIGHT_VERSION が不正です: $REDIS_INSIGHT_VERSION" ;; esac
  for _k in WITH_BROWSER WITH_NGINX WITH_CLOUDFLARED WITH_REDIS_UI; do
    eval "_v=\$$_k"
    case "$_v" in 0|1) ;; *) die "$_k は 0 か 1 で指定してください: $_v" ;; esac
  done
  case "$DOWNLOAD_DIR" in /*) ;; *) die "DOWNLOAD_DIR は絶対パスで指定してください: $DOWNLOAD_DIR" ;; esac
  case "$COOKIE_DIR" in /*) ;; *) die "COOKIE_DIR は絶対パスで指定してください: $COOKIE_DIR" ;; esac
  case "$DOWNLOAD_DIR/" in "$DATA_DIR"/*) die "DOWNLOAD_DIR は $DATA_DIR の配下にできません（purge で動画が消えるため）: $DOWNLOAD_DIR" ;; esac
  if [ "$WITH_CLOUDFLARED" = 1 ] && [ -z "$CLOUDFLARE_TOKEN" ]; then
    die "WITH_CLOUDFLARED=1 には CLOUDFLARE_TOKEN が必要です"
  fi
}

install_nodejs() {
  _major=$(node -v 2>/dev/null | sed 's/^v//; s/\..*//' || true)
  [ "${_major:-0}" -lt 24 ] || return 0
  log "Node.js 24 を導入します"
  install -d -m 755 /etc/apt/keyrings
  curl -fsSL https://deb.nodesource.com/gpgkey/nodesource-repo.gpg.key | gpg --dearmor --yes -o /etc/apt/keyrings/nodesource.gpg
  printf 'deb [signed-by=/etc/apt/keyrings/nodesource.gpg] https://deb.nodesource.com/node_24.x nodistro main\n' > /etc/apt/sources.list.d/nodesource.list
  DEBIAN_FRONTEND=noninteractive apt-get update -qq
  apt_install nodejs
}

install_packages() {
  log "パッケージを導入します"
  DEBIAN_FRONTEND=noninteractive apt-get update -qq
  apt_install python3 python3-venv python3-pip ffmpeg redis-server redis-tools git curl ca-certificates gnupg jq openssl iproute2 xz-utils
  install_nodejs
}

setup_python() {
  log "Python 環境を構築します"
  [ -x "$DIR/venv/bin/python3" ] || python3 -m venv "$DIR/venv"
  "$DIR/venv/bin/pip" install -q --no-cache-dir \
    -r "$DIR/apiServer/requirements.txt" -r "$DIR/workerServer/requirements.txt"
  sed "s#http://pot-provider:4416#http://127.0.0.1:$POT_PORT#" "$DIR/workerServer/yt-dlp.conf" > /etc/yt-dlp.conf
  _site=$("$DIR/venv/bin/python3" -c "import sysconfig; print(sysconfig.get_paths()['purelib'])")
  [ -d "$_site/yt_dlp_plugins" ] || die "bgutil-ytdlp-pot-provider のプラグインが見つかりません"
  rm -rf "$PLUGIN_DIR"
  mkdir -p "$PLUGIN_DIR"
  cp -r "$_site/yt_dlp_plugins" "$PLUGIN_DIR/"
  grep -q '^--plugin-dirs ' /etc/yt-dlp.conf || printf -- '--plugin-dirs %s\n' "$PLUGIN_DIR" >> /etc/yt-dlp.conf
}

setup_ytdlp() {
  log "yt-dlp を導入します"
  mkdir -p "$DIR/ytdlp"
  YTDLP_DIR="$DIR/ytdlp" YTDLP_REPO="$YTDLP_REPO" "$DIR/venv/bin/python3" "$DIR/workerServer/src/updater.py"
}

setup_pot() {
  log "pot-provider を構築します"
  _ver=$("$DIR/venv/bin/pip" show bgutil-ytdlp-pot-provider | sed -n 's/^Version: //p')
  [ -n "$_ver" ] || die "bgutil-ytdlp-pot-provider の版数を取得できません"
  _pot="$DIR/pot-provider"
  if [ -d "$_pot/.git" ] && [ "$(git -C "$_pot" describe --tags 2>/dev/null || true)" = "$_ver" ]; then
    [ -f "$_pot/server/build/main.js" ] && return 0
  else
    rm -rf "$_pot"
    git clone -q --depth 1 --branch "$_ver" https://github.com/Brainicism/bgutil-ytdlp-pot-provider "$_pot"
  fi
  (cd "$_pot/server" && npm ci --no-audit --no-fund && npx tsc)
  [ -f "$_pot/server/build/main.js" ] || die "pot-provider のビルドに失敗しました"
}

setup_dirs() {
  mkdir -p "$DATA_DIR" "$DOWNLOAD_DIR" "$COOKIE_DIR"
  chmod 700 "$COOKIE_DIR"
}

setup_redis() {
  log "redis を設定します"
  grep -qx 'bind 127.0.0.1' /etc/redis/redis.conf && return 0
  if grep -q '^bind ' /etc/redis/redis.conf; then
    sed -i 's/^bind .*/bind 127.0.0.1/' /etc/redis/redis.conf
  else
    printf 'bind 127.0.0.1\n' >> /etc/redis/redis.conf
  fi
  REDIS_CHANGED=1
}

render_unit() {
  _name=$1; _exec=${2:-}
  sed "s|@@DIR@@|$DIR|g; s|@@EXEC@@|$_exec|g" "$SCRIPT_DIR/units/$_name.service" > "$UNIT_DIR/$_name.service"
}

remove_unit() {
  if unit_exists "$1"; then
    systemctl disable --now "$1" >/dev/null 2>&1 || true
    rm -f "$UNIT_DIR/$1.service"
    systemctl daemon-reload
  fi
}

start_units() {
  for _u in "$@"; do
    systemctl enable "$_u" >/dev/null 2>&1
    systemctl reset-failed "$_u" >/dev/null 2>&1 || true
    systemctl restart "$_u" || warn "$_u の起動に失敗しました（journalctl -u $_u を確認してください）"
  done
}

setup_units() {
  for _n in pot api dispatcher; do render_unit "ytdlpserver-$_n"; done
  systemctl daemon-reload
}

start_core() {
  if [ "${REDIS_CHANGED:-0}" = 1 ] || ! systemctl is-active --quiet redis-server; then
    start_units redis-server
  else
    systemctl enable redis-server >/dev/null 2>&1
  fi
  start_units ytdlpserver-pot ytdlpserver-api ytdlpserver-dispatcher
}

setup_browser() {
  log "ログイン用ブラウザを導入します"
  apt_install xvfb fonts-noto-cjk tzdata
  "$DIR/venv/bin/pip" install -q --no-cache-dir -r "$DIR/browserServer/requirements.txt" playwright
  PLAYWRIGHT_BROWSERS_PATH="$PLAYWRIGHT_DIR" "$DIR/venv/bin/playwright" install --with-deps chromium
  _chrome=$(find "$PLAYWRIGHT_DIR" -type f -name chrome -path '*chromium-*' | head -n 1)
  [ -n "$_chrome" ] || die "Playwright の chromium が見つかりません"
  mkdir -p "$DIR/bin"
  ln -sf "$_chrome" "$DIR/bin/chromium"
  render_unit ytdlpserver-browser
  systemctl daemon-reload
  start_units ytdlpserver-browser
}

browser_allowed() {
  [ "$(mem_mb)" -ge 2048 ] && [ "$(free_mb /)" -ge 1024 ] && return 0
  if set_has WITH_BROWSER || env_has WITH_BROWSER; then
    warn "リソースが少ない環境です（メモリ $(mem_mb)MB・ルート空き $(free_mb /)MB）。メモリ 2048MB・空き 1024MB 程度を推奨します"
    return 0
  fi
  warn "リソース不足のため、ログイン用ブラウザの導入を見送ります（使うには WITH_BROWSER=1 を明示してください）"
  return 1
}

setup_nginx() {
  log "nginx を設定します"
  apt_install nginx openssl
  mkdir -p /etc/nginx/certs
  if [ ! -f /etc/nginx/certs/server.crt ]; then
    openssl req -x509 -nodes -days 3650 -newkey rsa:2048 \
      -keyout /etc/nginx/certs/server.key -out /etc/nginx/certs/server.crt \
      -subj "/C=US/ST=State/L=City/O=SelfSigned/CN=$SSL_CN" >/dev/null 2>&1
    chmod 600 /etc/nginx/certs/server.key
  fi
  sed "s#http://api:5000#http://127.0.0.1:$API_PORT#" "$DIR/nginx/conf.d/default.conf" > "$NGINX_CONF"
  rm -f /etc/nginx/sites-enabled/default
  nginx -t
  start_units nginx
}

teardown_nginx() {
  [ -f "$NGINX_CONF" ] || return 0
  rm -f "$NGINX_CONF"
  systemctl disable --now nginx >/dev/null 2>&1 || true
}

setup_cloudflared() {
  log "cloudflared を導入します"
  case "$(uname -m)" in x86_64) _arch=amd64 ;; aarch64) _arch=arm64 ;; esac
  _asset="cloudflared-linux-$_arch"
  _json=$(curl -fsSL https://api.github.com/repos/cloudflare/cloudflared/releases/latest) || die "cloudflared の版数情報を取得できません"
  _url=$(printf '%s' "$_json" | jq -r --arg n "$_asset" '.assets[] | select(.name == $n) | .browser_download_url')
  _digest=$(printf '%s' "$_json" | jq -r --arg n "$_asset" '.assets[] | select(.name == $n) | (.digest // empty)')
  [ -n "$_url" ] || die "cloudflared のダウンロード URL を取得できません（$_asset）"
  _tmp=$(mktemp)
  curl -fsSL -o "$_tmp" "$_url"
  case "$_digest" in
    sha256:*)
      printf '%s  %s\n' "${_digest#sha256:}" "$_tmp" | sha256sum -c - >/dev/null || { rm -f "$_tmp"; die "cloudflared の SHA256 が一致しません"; }
      ;;
    *) warn "ダイジェストを取得できないため、cloudflared の SHA256 検証を省略します" ;;
  esac
  install -m 755 "$_tmp" /usr/local/bin/cloudflared
  rm -f "$_tmp"
  render_unit ytdlpserver-cloudflared
  systemctl daemon-reload
  start_units ytdlpserver-cloudflared
}

teardown_cloudflared() {
  remove_unit ytdlpserver-cloudflared
  rm -f /usr/local/bin/cloudflared
}

build_redis_insight() {
  _ver=$REDIS_INSIGHT_VERSION
  _dest="$DIR/redisinsight"
  if [ -f "$_dest/.version" ] && [ "$(cat "$_dest/.version")" = "$_ver" ] && [ -f "$_dest/api/dist/src/main.js" ]; then
    return 0
  fi
  [ "$(node -v | sed 's/^v//; s/\..*//')" -ge 24 ] || { warn "Redis Insight には Node.js 24 以上が必要です"; return 1; }
  _mem=$(mem_mb)
  [ "$_mem" -ge 1800 ] || { warn "Redis Insight のビルドにはメモリ 2GB 程度が必要です（${_mem}MB）"; return 1; }
  _work="$DIR/ri-build"
  umount "$_work" 2>/dev/null || true
  rm -rf "$_work"
  mkdir -p "$_work"
  _storage=$RI_BUILD_STORAGE
  if [ "$_storage" != disk ]; then
    if [ "$_mem" -lt 12000 ]; then
      [ "$_storage" != tmpfs ] || { warn "tmpfs でのビルドにはメモリ 12000MB 程度が必要です（${_mem}MB）"; return 1; }
      _storage=disk
    elif mount -t tmpfs -o size=11000m tmpfs "$_work" 2>/dev/null; then
      _storage=tmpfs
    elif [ "$_storage" = tmpfs ]; then
      warn "tmpfs をマウントできません"
      return 1
    else
      _storage=disk
    fi
  fi
  if [ "$_storage" = disk ] && [ "$(free_mb "$_work")" -lt 10000 ]; then
    warn "ビルドにはディスクの空き 10000MB 程度が必要です（$(free_mb "$_work")MB）"
    rm -rf "$_work"
    return 1
  fi
  log "Redis Insight $_ver をビルドします（作業領域: $_storage、数分かかります）"
  apt_install python3-setuptools make g++ build-essential || return 1
  if RI_WORK="$_work" RI_VER="$_ver" RI_DEST="$_dest" sh "$SCRIPT_DIR/build-redisinsight.sh"; then _rc=0; else _rc=1; fi
  [ "$_storage" != tmpfs ] || umount "$_work"
  rm -rf "$_work"
  [ "$_rc" = 0 ] || warn "Redis Insight のビルドに失敗しました"
  return "$_rc"
}

setup_redis_ui() {
  _run=redis-commander
  if [ "$REDIS_UI" = insight ]; then
    if build_redis_insight; then
      _run=redis-insight
      mkdir -p "$RI_DATA_DIR"
    else
      warn "Redis Insight を導入できないため redis-commander で継続します"
    fi
  fi
  if [ "$_run" = redis-commander ]; then
    npm install -g --no-audit --no-fund redis-commander
  fi
  render_unit ytdlpserver-redis-ui "$DIR/installer/run/$_run.sh"
  systemctl daemon-reload
  start_units ytdlpserver-redis-ui
}

converge_optional() {
  if [ "$WITH_BROWSER" = 1 ] && browser_allowed; then setup_browser; else remove_unit ytdlpserver-browser; fi
  if [ "$WITH_NGINX" = 1 ]; then setup_nginx; else teardown_nginx; fi
  if [ "$WITH_CLOUDFLARED" = 1 ]; then setup_cloudflared; else teardown_cloudflared; fi
  if [ "$WITH_REDIS_UI" = 1 ]; then setup_redis_ui; else remove_unit ytdlpserver-redis-ui; fi
}

load_config() {
  [ -f "$CONF_FILE" ] || return 0
  # shellcheck source=/dev/null
  . "$CONF_FILE"
}

app_run_install() {
  load_config
  check_env
  check_values
  install_packages
  setup_python
  setup_ytdlp
  setup_pot
  setup_dirs
  setup_redis
  setup_units
  start_core
  converge_optional
  _ip=$(ip -4 route get 1.1.1.1 2>/dev/null | sed -n 's/.* src \([0-9.]*\).*/\1/p')
  log "API: http://${_ip:-<IPアドレス>}:$API_PORT/download"
  log "設定: $CONF_FILE（編集後は systemctl restart ytdlpserver-api ytdlpserver-dispatcher）"
}

app_run_uninstall() {
  for _f in "$UNIT_DIR"/ytdlpserver-*.service; do
    [ -f "$_f" ] || continue
    remove_unit "$(basename "$_f" .service)"
  done
  teardown_nginx
  rm -f /usr/local/bin/cloudflared /etc/yt-dlp.conf
  rm -rf /etc/yt-dlp/plugins/bgutil
}

app_run_purge() {
  load_config
  systemctl stop redis-server >/dev/null 2>&1 || true
  rm -rf /var/lib/redis/dump.rdb /var/lib/redis/appendonly.aof /var/lib/redis/appendonlydir
  systemctl start redis-server >/dev/null 2>&1 || true
  case "${COOKIE_DIR:-}" in
    ""|/|/usr|/etc|/var|/opt|/home|/root|/mnt) ;;
    *) rm -rf "$COOKIE_DIR" ;;
  esac
}
