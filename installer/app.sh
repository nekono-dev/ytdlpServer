# shellcheck shell=sh disable=SC2154
# shellcheck source=/dev/null
. "$SCRIPT_DIR/host-service.sh"

SVC_USER=$APP
RI_DATA_DIR="$DATA_DIR/redisinsight"
NGINX_CONF=/etc/nginx/conf.d/ytdlpserver.conf
PLUGIN_DIR=/etc/yt-dlp/plugins/bgutil
PLAYWRIGHT_DIR="$DIR/browsers"

mem_mb() { awk '/^MemTotal:/ {print int($2 / 1024)}' /proc/meminfo; }
free_mb() { df -Pm "$1" | awk 'NR==2 {print $4}'; }

check_env() {
  hs_supported ubuntu:24.04
  [ "$HS_INIT" = systemd ] || die "systemd が動作していません"
  case "$(uname -m)" in x86_64|aarch64) ;; *) die "未対応のアーキテクチャです: $(uname -m)" ;; esac
  [ "$(mem_mb)" -ge 1024 ] || die "メモリが不足しています（$(mem_mb)MB）。1024MB 以上を割り当ててください"
}

app_check() {
  check_env
  for _k in WORKER_MAX API_PORT POT_PORT REDIS_TTL RETRY_COUNT UPDATE_INTERVAL UPDATE_RETRY_INTERVAL \
    UPDATE_COOLDOWN KEEP_VERSIONS DISPATCH_SCAN_INTERVAL LEASE_TTL HEARTBEAT_INTERVAL STOP_GRACE \
    INPROGRESS_STALE BROWSER_PORT SESSION_TIMEOUT; do
    hs_expect_int "$_k" "$(config_value "$_k")"
  done
  hs_expect_int WORKER_MAX "$(config_value WORKER_MAX)" 1
  case "$(config_value YTDLP_REPO)" in */*) ;; *) die "YTDLP_REPO は 所有者/リポジトリ の形で指定してください: $(config_value YTDLP_REPO)" ;; esac
  hs_expect_one_of REDIS_UI "$(config_value REDIS_UI)" insight commander
  hs_expect_one_of RI_BUILD_STORAGE "$(config_value RI_BUILD_STORAGE)" tmpfs disk auto
  case "$(config_value REDIS_INSIGHT_VERSION)" in ''|*[!0-9A-Za-z._-]*) die "REDIS_INSIGHT_VERSION が不正です: $(config_value REDIS_INSIGHT_VERSION)" ;; esac
  for _k in WITH_BROWSER WITH_NGINX WITH_CLOUDFLARED WITH_REDIS_UI; do
    hs_expect_bool "$_k" "$(config_value "$_k")"
  done
  _dl=$(config_value DOWNLOAD_DIR)
  hs_expect_abs_path DOWNLOAD_DIR "$_dl"
  hs_expect_abs_path COOKIE_DIR "$(config_value COOKIE_DIR)"
  case "$_dl/" in "$DATA_DIR"/*) die "DOWNLOAD_DIR は $DATA_DIR の配下にできません（purge で動画が消えるため）: $_dl" ;; esac
  if [ "$(config_value WITH_CLOUDFLARED)" = 1 ] && [ -z "$(config_value CLOUDFLARE_TOKEN)" ]; then
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
  DEBIAN_FRONTEND=noninteractive apt-get install -y -qq --no-install-recommends nodejs
}

install_packages() {
  hs_pkg python3 python3-venv python3-pip ffmpeg redis-server redis-tools git curl ca-certificates gnupg jq openssl iproute2 xz-utils
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
  hs_dir "$DIR/ytdlp" "$SVC_USER"
  YTDLP_DIR="$DIR/ytdlp" YTDLP_REPO="$YTDLP_REPO" "$DIR/venv/bin/python3" "$DIR/workerServer/src/updater.py"
  chown -R "$SVC_USER:$SVC_USER" "$DIR/ytdlp"
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
  hs_user "$SVC_USER"
  hs_dir "$DATA_DIR" "$SVC_USER" 750
  hs_dir "$DOWNLOAD_DIR" "$SVC_USER"
  hs_dir "$COOKIE_DIR" "$SVC_USER" 700
  chown -R "$SVC_USER:$SVC_USER" "$DATA_DIR" "$COOKIE_DIR"
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

core_service() {
  hs_service "ytdlpserver-$1" --description "$2" --user "$SVC_USER" \
    --requires redis-server --after ytdlpserver-pot -- "$DIR/installer/run/$1.sh"
}

start_core() {
  if [ "${REDIS_CHANGED:-0}" = 1 ]; then hs_start redis-server; else hs_ensure redis-server; fi
  hs_service ytdlpserver-pot --description "ytdlpServer pot-provider" --user "$SVC_USER" -- "$DIR/installer/run/pot.sh"
  core_service api "ytdlpServer API"
  core_service dispatcher "ytdlpServer dispatcher"
  hs_start ytdlpserver-pot ytdlpserver-api ytdlpserver-dispatcher
}

start_optional() {
  (hs_start "$1") || warn "$1 の起動に失敗しました（$(hs_logs "$1") を確認してください）"
}

setup_browser() {
  log "ログイン用ブラウザを導入します"
  hs_pkg xvfb fonts-noto-cjk tzdata
  "$DIR/venv/bin/pip" install -q --no-cache-dir -r "$DIR/browserServer/requirements.txt" playwright
  PLAYWRIGHT_BROWSERS_PATH="$PLAYWRIGHT_DIR" "$DIR/venv/bin/playwright" install --with-deps chromium
  _chrome=$(find "$PLAYWRIGHT_DIR" -type f -name chrome -path '*chromium-*' | head -n 1)
  [ -n "$_chrome" ] || die "Playwright の chromium が見つかりません"
  mkdir -p "$DIR/bin"
  ln -sf "$_chrome" "$DIR/bin/chromium"
  hs_service ytdlpserver-browser --description "ytdlpServer login browser" --user "$SVC_USER" -- "$DIR/installer/run/browser.sh"
  start_optional ytdlpserver-browser
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
  hs_pkg nginx openssl
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
  start_optional nginx
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
  hs_service ytdlpserver-cloudflared --description "ytdlpServer Cloudflare Tunnel" --user "$SVC_USER" \
    --after ytdlpserver-api -- "$DIR/installer/run/cloudflared.sh"
  start_optional ytdlpserver-cloudflared
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
  (hs_pkg python3-setuptools make g++ build-essential) || return 1
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
      hs_dir "$RI_DATA_DIR" "$SVC_USER"
    else
      warn "Redis Insight を導入できないため redis-commander で継続します"
    fi
  fi
  if [ "$_run" = redis-commander ]; then
    npm install -g --no-audit --no-fund redis-commander
  fi
  hs_service ytdlpserver-redis-ui --description "ytdlpServer Redis Web UI" --user "$SVC_USER" \
    --requires redis-server -- "$DIR/installer/run/$_run.sh"
  start_optional ytdlpserver-redis-ui
}

converge_optional() {
  if [ "$WITH_BROWSER" = 1 ] && browser_allowed; then setup_browser; fi
  if [ "$WITH_NGINX" = 1 ]; then setup_nginx; else teardown_nginx; fi
  if [ "$WITH_CLOUDFLARED" = 1 ]; then setup_cloudflared; else rm -f /usr/local/bin/cloudflared; fi
  if [ "$WITH_REDIS_UI" = 1 ]; then setup_redis_ui; fi
}

app_install() {
  check_env
  # shellcheck source=/dev/null
  . "$CONF_FILE"
  install_packages
  setup_dirs
  setup_python
  setup_ytdlp
  setup_pot
  setup_redis
  start_core
  converge_optional
  hs_prune
  _ip=$(ip -4 route get 1.1.1.1 2>/dev/null | sed -n 's/.* src \([0-9.]*\).*/\1/p')
  log "API: http://${_ip:-<IPアドレス>}:$API_PORT/download"
  log "設定: $CONF_FILE（編集後は sudo ytdlpserver-ctl update で反映）"
}

app_uninstall() {
  hs_remove_all
  teardown_nginx
  rm -f /usr/local/bin/cloudflared /etc/yt-dlp.conf
  rm -rf /etc/yt-dlp/plugins/bgutil
}

app_purge() {
  # shellcheck source=/dev/null
  [ ! -f "$CONF_FILE" ] || . "$CONF_FILE"
  systemctl stop redis-server >/dev/null 2>&1 || true
  rm -rf /var/lib/redis/dump.rdb /var/lib/redis/appendonly.aof /var/lib/redis/appendonlydir
  systemctl start redis-server >/dev/null 2>&1 || true
  case "${COOKIE_DIR:-}" in
    ""|/|/usr|/etc|/var|/opt|/home|/root|/mnt) ;;
    *) rm -rf "$COOKIE_DIR" ;;
  esac
  hs_user_remove "$SVC_USER"
}
