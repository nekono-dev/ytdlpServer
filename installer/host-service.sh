# shellcheck shell=sh disable=SC2154

HS_OS=''
HS_INIT=''
HS_OS_ID=''
HS_OS_VERSION=''
HS_DEFINED=''
HS_RELOAD=''
HS_APT_UPDATED=''
HS_MARK="$APP"
HS_SYSTEMD_DIR=/etc/systemd/system
HS_OPENRC_DIR=/etc/init.d

hs_os_field() {
  sed -n "s/^$1=//p" /etc/os-release | tr -d '"'
}

hs_init() {
  [ -z "$HS_OS" ] || return 0
  [ "$INSTALL_MODE" = system ] || die "ホストのサービスとして導入するには、app.conf の INSTALL_MODE を system にしてください"
  [ "$(id -u)" -eq 0 ] || die "root で実行してください"
  [ -r /etc/os-release ] || die "/etc/os-release がありません"
  HS_OS_ID=$(hs_os_field ID)
  HS_OS_VERSION=$(hs_os_field VERSION_ID)
  case " $HS_OS_ID $(hs_os_field ID_LIKE) " in
    *" alpine "*) HS_OS=alpine ;;
    *" debian "*|*" ubuntu "*) HS_OS=debian ;;
    *) die "未対応の OS です: ${HS_OS_ID:-不明}（Debian 系と Alpine に対応しています）" ;;
  esac
  if [ -d /run/systemd/system ]; then
    HS_INIT=systemd
  elif command -v openrc-run >/dev/null 2>&1 && command -v rc-service >/dev/null 2>&1; then
    HS_INIT=openrc
  else
    HS_OS=''
    die "systemd も OpenRC も動作していません"
  fi
}

hs_supported() {
  hs_init
  for _hs_s in "$@"; do
    case "$_hs_s" in
      *:*) [ "$HS_OS_ID:$HS_OS_VERSION" = "$_hs_s" ] && return 0 ;;
      *) [ "$HS_OS_ID" = "$_hs_s" ] && return 0 ;;
    esac
  done
  die "この OS には対応していません: $HS_OS_ID $HS_OS_VERSION（対応: $*）"
}

hs_word() {
  case "$2" in
    ''|*[!A-Za-z0-9_./:=@+,-]*) die "$1 に使えない文字が含まれています（英数字と _ . / : = @ + , - のみ）: $2" ;;
  esac
}

hs_text() {
  case "$2" in
    *'"'*|*'$'*|*'`'*|*'\'*|*'%'*) die "$1 に \" \$ \` \\ % は使えません: $2" ;;
  esac
  case "$2" in
    *'
'*) die "$1 に改行は使えません" ;;
  esac
}

hs_service_name() {
  case "$1" in
    "$APP"|"$APP"-*) hs_word "サービス名" "$1" ;;
    *) die "サービス名は $APP か $APP-<名前> にしてください: $1" ;;
  esac
}

hs_pkg_missing() {
  for _hs_p in "$@"; do
    case "$HS_OS" in
      debian) [ "$(dpkg-query -W -f='${Status}' "$_hs_p" 2>/dev/null)" = "install ok installed" ] || return 0 ;;
      alpine) apk info -e "$_hs_p" >/dev/null 2>&1 || return 0 ;;
    esac
  done
  return 1
}

hs_pkg() {
  hs_init
  [ "$#" -gt 0 ] || return 0
  hs_pkg_missing "$@" || return 0
  log "パッケージを導入: $*"
  case "$HS_OS" in
    debian)
      if [ -z "$HS_APT_UPDATED" ]; then
        DEBIAN_FRONTEND=noninteractive apt-get update -qq || die "apt-get update に失敗しました"
        HS_APT_UPDATED=1
      fi
      DEBIAN_FRONTEND=noninteractive apt-get install -y -qq --no-install-recommends "$@" >/dev/null || die "パッケージを導入できません: $*"
      ;;
    alpine)
      apk add --no-cache -q "$@" || die "パッケージを導入できません: $*"
      ;;
  esac
}

hs_group_exists() { grep -q "^$1:" /etc/group; }

hs_user() {
  hs_init
  _hs_u=$1; _hs_h=${2:-$DATA_DIR}
  case "$_hs_u" in
    root) return 0 ;;
    ''|[!a-z_]*|*[!a-z0-9_-]*) die "ユーザー名が不正です（英小文字・数字・_・-）: $_hs_u" ;;
  esac
  id "$_hs_u" >/dev/null 2>&1 && return 0
  log "ユーザーを作成: $_hs_u"
  case "$HS_OS" in
    alpine)
      hs_group_exists "$_hs_u" || addgroup -S "$_hs_u"
      adduser -S -D -H -h "$_hs_h" -s /sbin/nologin -G "$_hs_u" "$_hs_u"
      ;;
    debian)
      if hs_group_exists "$_hs_u"; then
        useradd --system --no-create-home --home-dir "$_hs_h" --shell /usr/sbin/nologin --gid "$_hs_u" "$_hs_u"
      else
        useradd --system --user-group --no-create-home --home-dir "$_hs_h" --shell /usr/sbin/nologin "$_hs_u"
      fi
      ;;
  esac
}

hs_user_remove() {
  hs_init
  _hs_u=$1
  [ "$_hs_u" != root ] || return 0
  if id "$_hs_u" >/dev/null 2>&1; then
    log "ユーザーを削除: $_hs_u"
    case "$HS_OS" in
      alpine) deluser "$_hs_u" >/dev/null 2>&1 || warn "ユーザーを削除できません: $_hs_u" ;;
      debian) userdel "$_hs_u" >/dev/null 2>&1 || warn "ユーザーを削除できません: $_hs_u" ;;
    esac
  fi
  if hs_group_exists "$_hs_u"; then
    case "$HS_OS" in
      alpine) delgroup "$_hs_u" >/dev/null 2>&1 || true ;;
      debian) groupdel "$_hs_u" >/dev/null 2>&1 || true ;;
    esac
  fi
}

hs_dir() {
  _hs_d=$1; _hs_o=${2:-root}; _hs_m=${3:-}
  case "$_hs_d" in
    /?*) ;;
    *) die "ディレクトリは絶対パスで指定してください: $_hs_d" ;;
  esac
  mkdir -p "$_hs_d"
  chown "$_hs_o:$_hs_o" "$_hs_d"
  [ -z "$_hs_m" ] || chmod "$_hs_m" "$_hs_d"
}

hs_conf_group() {
  [ -f "$CONF_FILE" ] || return 0
  if [ "$1" = root ]; then
    chgrp root "$CONF_FILE"
    chmod 600 "$CONF_FILE"
  else
    chgrp "$1" "$CONF_FILE"
    chmod 640 "$CONF_FILE"
  fi
}

hs_unit_name() {
  case "$1" in
    *.*) printf '%s' "$1" ;;
    *) printf '%s.service' "$1" ;;
  esac
}

hs_units() {
  _hs_r=''
  for _hs_x in $1; do _hs_r="$_hs_r $(hs_unit_name "$_hs_x")"; done
  printf '%s' "$_hs_r"
}

hs_render_systemd() {
  printf '[Unit]\n'
  printf 'Description=%s\n' "$_hs_desc"
  printf 'After=network-online.target%s%s%s\n' "$(hs_units "$_hs_after")" "$(hs_units "$_hs_req")" "$(hs_units "$_hs_wants")"
  printf 'Wants=network-online.target%s\n' "$(hs_units "$_hs_wants")"
  [ -z "$_hs_req" ] || printf 'Requires=%s\n' "$(hs_units "$_hs_req" | sed 's/^ //')"
  printf 'X-HostService=%s\n' "$HS_MARK"
  printf '\n[Service]\n'
  printf 'Type=simple\n'
  if [ "$_hs_user" != root ]; then
    printf 'User=%s\n' "$_hs_user"
    printf 'Group=%s\n' "$_hs_user"
  fi
  printf 'WorkingDirectory=%s\n' "$_hs_wd"
  printf 'Environment=HS_APP=%s HS_DIR=%s HS_CONF_FILE=%s HS_DATA_DIR=%s\n' "$APP" "$DIR" "$CONF_FILE" "$DATA_DIR"
  for _hs_e in $_hs_env; do printf 'Environment=%s\n' "$_hs_e"; done
  printf 'ExecStart=%s\n' "$_hs_cmd"
  printf 'Restart=always\n'
  printf 'RestartSec=3\n'
  printf 'SyslogIdentifier=%s\n' "$_hs_name"
  printf '%s' "$_hs_systemd"
  printf '\n[Install]\n'
  printf 'WantedBy=multi-user.target\n'
}

hs_render_openrc() {
  _hs_bin=${_hs_cmd%% *}
  _hs_args=''
  case "$_hs_cmd" in *' '*) _hs_args=${_hs_cmd#* } ;; esac
  printf '#!/sbin/openrc-run\n'
  printf 'hs_app="%s"\n' "$HS_MARK"
  printf 'name="%s"\n' "$_hs_name"
  printf 'description="%s"\n' "$_hs_desc"
  printf 'supervisor=supervise-daemon\n'
  printf 'command="%s"\n' "$_hs_bin"
  [ -z "$_hs_args" ] || printf 'command_args="%s"\n' "$_hs_args"
  [ "$_hs_user" = root ] || printf 'command_user="%s:%s"\n' "$_hs_user" "$_hs_user"
  printf 'directory="%s"\n' "$_hs_wd"
  printf 'supervise_daemon_args="--env HS_APP=%s --env HS_DIR=%s --env HS_CONF_FILE=%s --env HS_DATA_DIR=%s' "$APP" "$DIR" "$CONF_FILE" "$DATA_DIR"
  for _hs_e in $_hs_env; do printf ' --env %s' "$_hs_e"; done
  printf '"\n'
  printf 'output_logger="logger -t %s -p daemon.info"\n' "$_hs_name"
  printf 'error_logger="logger -t %s -p daemon.err"\n' "$_hs_name"
  printf 'respawn_delay=3\n'
  printf 'respawn_max=0\n'
  printf '%s' "$_hs_openrc"
  printf '\ndepend() {\n'
  printf '\tuse net logger%s\n' "$_hs_wants"
  [ -z "$_hs_req" ] || printf '\tneed%s\n' "$_hs_req"
  [ -z "$_hs_after" ] || printf '\tafter%s\n' "$_hs_after"
  printf '}\n'
}

hs_install_file() {
  _hs_tmp="$2.hs-new"
  cat > "$_hs_tmp"
  chmod "$3" "$_hs_tmp"
  if [ -f "$2" ] && cmp -s "$_hs_tmp" "$2"; then
    rm -f "$_hs_tmp"
    return 1
  fi
  mv -f "$_hs_tmp" "$2"
}

hs_need_arg() {
  [ "$2" -ge 2 ] || die "hs_service: $1 に値がありません"
}

hs_service() {
  hs_init
  [ "$#" -gt 0 ] || die "hs_service: サービス名がありません"
  _hs_name=$1; shift
  hs_service_name "$_hs_name"
  _hs_desc=$_hs_name; _hs_user=root; _hs_wd=$DIR; _hs_after=''; _hs_req=''; _hs_wants=''; _hs_env=''; _hs_systemd=''; _hs_openrc=''
  while [ "$#" -gt 0 ]; do
    case "$1" in
      --description) hs_need_arg "$1" "$#"; hs_text "$1" "$2"; _hs_desc=$2; shift 2 ;;
      --user) hs_need_arg "$1" "$#"; _hs_user=$2; shift 2 ;;
      --dir) hs_need_arg "$1" "$#"; hs_word "$1" "$2"; _hs_wd=$2; shift 2 ;;
      --after) hs_need_arg "$1" "$#"; hs_word "$1" "$2"; _hs_after="$_hs_after $2"; shift 2 ;;
      --requires) hs_need_arg "$1" "$#"; hs_word "$1" "$2"; _hs_req="$_hs_req $2"; shift 2 ;;
      --wants) hs_need_arg "$1" "$#"; hs_word "$1" "$2"; _hs_wants="$_hs_wants $2"; shift 2 ;;
      --env)
        hs_need_arg "$1" "$#"; hs_word "$1" "$2"
        case "$2" in [A-Za-z_]*=*) ;; *) die "hs_service: --env は KEY=VALUE の形にしてください: $2" ;; esac
        _hs_env="$_hs_env $2"; shift 2 ;;
      --systemd) hs_need_arg "$1" "$#"; hs_text "$1" "$2"; _hs_systemd="$_hs_systemd$2
"; shift 2 ;;
      --openrc) hs_need_arg "$1" "$#"; hs_text "$1" "$2"; _hs_openrc="$_hs_openrc$2
"; shift 2 ;;
      --) shift; break ;;
      *) die "hs_service: 不明なオプションです: $1" ;;
    esac
  done
  [ "$#" -gt 0 ] || die "hs_service: 実行するコマンドがありません（-- の後に書きます）"
  case "$1" in
    /*) ;;
    *) die "hs_service: コマンドは絶対パスで指定してください: $1" ;;
  esac
  for _hs_a in "$@"; do hs_word "コマンドの引数" "$_hs_a"; done
  _hs_cmd=$*
  for _hs_p in "$DIR" "$CONF_FILE" "$DATA_DIR"; do hs_word "導入先のパス" "$_hs_p"; done
  [ -x "$1" ] || die "hs_service: 実行できません: $1"
  if [ "$_hs_user" != root ]; then
    id "$_hs_user" >/dev/null 2>&1 || die "hs_service: ユーザーがありません（先に hs_user を呼びます）: $_hs_user"
  fi
  hs_conf_group "$_hs_user"
  case "$HS_INIT" in
    systemd)
      if hs_render_systemd | hs_install_file "$_hs_name" "$HS_SYSTEMD_DIR/$_hs_name.service" 644; then HS_RELOAD=1; fi
      ;;
    openrc)
      hs_render_openrc | hs_install_file "$_hs_name" "$HS_OPENRC_DIR/$_hs_name" 755 || true
      ;;
  esac
  case " $HS_DEFINED " in
    *" $_hs_name "*) ;;
    *) HS_DEFINED="$HS_DEFINED $_hs_name" ;;
  esac
}

hs_reload() {
  [ "$HS_INIT" = systemd ] && [ -n "$HS_RELOAD" ] || return 0
  systemctl daemon-reload
  HS_RELOAD=''
}

hs_logs() {
  case "$HS_INIT" in
    systemd) printf 'journalctl -u %s' "$1" ;;
    openrc) printf 'syslog（/var/log/messages の %s の行）' "$1" ;;
  esac
}

hs_start() {
  hs_init
  hs_reload
  for _hs_s in "$@"; do
    log "サービスを起動: $_hs_s"
    case "$HS_INIT" in
      systemd)
        systemctl enable -q "$_hs_s" || die "サービスを有効にできません: $_hs_s"
        systemctl reset-failed "$_hs_s" >/dev/null 2>&1 || true
        systemctl restart "$_hs_s" || die "サービスを起動できません: $_hs_s（$(hs_logs "$_hs_s") を確認してください）"
        ;;
      openrc)
        rc-update add "$_hs_s" default >/dev/null 2>&1 || true
        rc-service "$_hs_s" restart >/dev/null || die "サービスを起動できません: $_hs_s（$(hs_logs "$_hs_s") を確認してください）"
        ;;
    esac
  done
}

hs_ensure() {
  hs_init
  hs_reload
  for _hs_s in "$@"; do
    case "$HS_INIT" in
      systemd)
        systemctl enable -q "$_hs_s" || die "サービスを有効にできません: $_hs_s"
        systemctl is-active -q "$_hs_s" || systemctl start "$_hs_s" || die "サービスを起動できません: $_hs_s（$(hs_logs "$_hs_s") を確認してください）"
        ;;
      openrc)
        rc-update add "$_hs_s" default >/dev/null 2>&1 || true
        rc-service "$_hs_s" status >/dev/null 2>&1 || rc-service "$_hs_s" start >/dev/null || die "サービスを起動できません: $_hs_s（$(hs_logs "$_hs_s") を確認してください）"
        ;;
    esac
  done
}

hs_owned() {
  case "$HS_INIT" in
    systemd) [ -f "$HS_SYSTEMD_DIR/$1.service" ] && grep -qx "X-HostService=$HS_MARK" "$HS_SYSTEMD_DIR/$1.service" ;;
    openrc) [ -f "$HS_OPENRC_DIR/$1" ] && grep -qx "hs_app=\"$HS_MARK\"" "$HS_OPENRC_DIR/$1" ;;
  esac
}

hs_remove_one() {
  hs_owned "$1" || return 0
  log "サービスを撤去: $1"
  case "$HS_INIT" in
    systemd)
      systemctl disable --now -q "$1" >/dev/null 2>&1 || true
      rm -f "$HS_SYSTEMD_DIR/$1.service"
      systemctl reset-failed "$1" >/dev/null 2>&1 || true
      HS_RELOAD=1
      ;;
    openrc)
      rc-service "$1" stop >/dev/null 2>&1 || true
      rc-update del "$1" default >/dev/null 2>&1 || true
      rm -f "$HS_OPENRC_DIR/$1"
      ;;
  esac
}

hs_remove() {
  hs_init
  for _hs_s in "$@"; do
    hs_service_name "$_hs_s"
    hs_remove_one "$_hs_s"
  done
  hs_reload
}

hs_list() {
  case "$HS_INIT" in
    systemd)
      for _hs_f in "$HS_SYSTEMD_DIR/$APP.service" "$HS_SYSTEMD_DIR/$APP"-*.service; do
        [ -f "$_hs_f" ] || continue
        _hs_f=${_hs_f##*/}
        printf '%s\n' "${_hs_f%.service}"
      done
      ;;
    openrc)
      for _hs_f in "$HS_OPENRC_DIR/$APP" "$HS_OPENRC_DIR/$APP"-*; do
        [ -f "$_hs_f" ] || continue
        printf '%s\n' "${_hs_f##*/}"
      done
      ;;
  esac
}

hs_prune() {
  hs_init
  for _hs_s in $(hs_list); do
    case " $HS_DEFINED " in
      *" $_hs_s "*) ;;
      *) hs_remove_one "$_hs_s" ;;
    esac
  done
  hs_reload
}

hs_remove_all() {
  hs_init
  for _hs_s in $(hs_list); do hs_remove_one "$_hs_s"; done
  hs_reload
}

hs_expect_int() {
  case "$2" in
    ''|*[!0-9]*) die "$1 は 0 以上の整数で指定してください: $2" ;;
  esac
  [ -z "${3:-}" ] || [ "$2" -ge "$3" ] || die "$1 は $3 以上にしてください: $2"
  [ -z "${4:-}" ] || [ "$2" -le "$4" ] || die "$1 は $4 以下にしてください: $2"
}

hs_expect_port() {
  hs_expect_int "$1" "$2" 1 65535
}

hs_expect_bool() {
  case "$2" in
    0|1) ;;
    *) die "$1 は 0 か 1 で指定してください: $2" ;;
  esac
}

hs_expect_one_of() {
  _hs_k=$1; _hs_v=$2; shift 2
  for _hs_c in "$@"; do [ "$_hs_v" = "$_hs_c" ] && return 0; done
  die "$_hs_k は $* のいずれかで指定してください: $_hs_v"
}

hs_expect_abs_path() {
  case "$2" in
    /?*) ;;
    *) die "$1 は絶対パスで指定してください: $2" ;;
  esac
}
