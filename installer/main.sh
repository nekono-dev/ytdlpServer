#!/bin/sh

set -eu

SCRIPT_DIR=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)
DIR=$(dirname -- "$SCRIPT_DIR")
SCHEMA="$SCRIPT_DIR/config.schema"

log() { printf '==> %s\n' "$*"; }
warn() { printf '警告: %s\n' "$*" 1>&2; }
die() { printf 'エラー: %s\n' "$*" 1>&2; exit 1; }

APP_NAME=''
INSTALL_MODE='system'
CTL_NAME=''
CTL_SUBCOMMAND=''
DATA_DIR=''
[ -f "$SCRIPT_DIR/app.conf" ] || die "アプリ定義がありません: $SCRIPT_DIR/app.conf"
# shellcheck source=/dev/null
. "$SCRIPT_DIR/app.conf"

valid_name() {
  case "$1" in
    ""|[!a-z]*|*[!a-z0-9-]*) return 1 ;;
  esac
}

valid_name "$APP_NAME" || die "app.conf の APP_NAME が不正です（英小文字・数字・-、英小文字始まり）: $APP_NAME"
APP=$APP_NAME
[ -n "$CTL_NAME" ] || CTL_NAME="$APP-ctl"
valid_name "$CTL_NAME" || die "app.conf の CTL_NAME が不正です: $CTL_NAME"
[ -z "$CTL_SUBCOMMAND" ] || valid_name "$CTL_SUBCOMMAND" || die "app.conf の CTL_SUBCOMMAND が不正です: $CTL_SUBCOMMAND"
CTL_CMD="$CTL_NAME${CTL_SUBCOMMAND:+ $CTL_SUBCOMMAND}"
case "$INSTALL_MODE" in
  system)
    CONF_DIR="/etc/$APP"
    STATE_DIR="/var/lib/$APP-installer"
    BIN_DIR='/usr/local/bin'
    [ -n "$DATA_DIR" ] || DATA_DIR="/var/lib/$APP"
    ;;
  user)
    [ -n "${HOME:-}" ] || die "HOME が未設定です"
    CONF_DIR="$HOME/.config/$APP"
    STATE_DIR="$HOME/.local/state/$APP-installer"
    BIN_DIR="$HOME/.local/bin"
    [ -n "$DATA_DIR" ] || DATA_DIR="$HOME/.local/state/$APP"
    ;;
  *) die "app.conf の INSTALL_MODE は system か user にしてください: $INSTALL_MODE" ;;
esac
case "$DATA_DIR" in
  /*) ;;
  *) die "app.conf の DATA_DIR は絶対パスにしてください: $DATA_DIR" ;;
esac
CONF_FILE="$CONF_DIR/$APP.env"
STATE_FILE="$STATE_DIR/state"
CTL="$BIN_DIR/$CTL_NAME"

usage() {
  cat <<EOF
使い方: $CTL_CMD <update|uninstall|status|refs> [オプション]
  update    [--ref NAME] [--set KEY=VALUE ...]   導入中のブランチ・タグの最新へ更新する。--ref で別のブランチ・タグへ切り替える
  uninstall [--purge]                            アプリ本体を削除する。--purge で設定・データ・導入メタ情報も削除する
  status                                         導入情報を表示する
  refs                                           切り替えられるブランチとタグを表示する
EOF
}

[ -f "$SCRIPT_DIR/app.sh" ] || die "アプリの処理がありません: $SCRIPT_DIR/app.sh"
# shellcheck source=/dev/null
. "$SCRIPT_DIR/app.sh"

check_user() {
  case "$INSTALL_MODE" in
    system) [ "$(id -u)" -eq 0 ] || die "root権限で実行してください" ;;
    user) [ "$(id -u)" -ne 0 ] || die "一般ユーザーで実行してください（sudo は不要です）" ;;
  esac
}

safe_to_remove() {
  case "$1" in
    ""|/|/usr|/usr/local|/usr/local/bin|/etc|/var|/var/lib|/opt|/home|/root|/tmp) return 1 ;;
  esac
  if [ -n "${HOME:-}" ]; then
    case "$1" in
      "$HOME"|"$HOME/.config"|"$HOME/.local"|"$HOME/.local/share"|"$HOME/.local/state"|"$HOME/.local/bin") return 1 ;;
    esac
  fi
  return 0
}

kv_get() {
  [ -f "$1" ] || return 1
  grep -q "^$2=" "$1" || return 1
  sed -n "s/^$2='\\(.*\\)'\$/\\1/p; t; s/^$2=\\(.*\\)\$/\\1/p" "$1" | tail -n 1
}

valid_key() {
  case "$1" in
    ""|[!A-Z]*|*[!A-Z0-9_]*) return 1 ;;
  esac
}

check_value() {
  case "$2" in
    *"'"*) die "$1 の値に ' は使えません" ;;
  esac
  [ "$(printf '%s' "$2" | wc -l)" -eq 0 ] || die "$1 の値に改行は使えません"
}

schema_lines() {
  [ -f "$SCHEMA" ] || die "設定スキーマがありません: $SCHEMA"
  grep -v '^[[:space:]]*\(#\|$\)' "$SCHEMA" || true
}

SETS=''

set_has() { printf '%s\n' "$SETS" | grep -q "^$1="; }
set_get() { printf '%s\n' "$SETS" | grep "^$1=" | tail -n 1 | sed "s/^$1=//"; }
env_has() { eval "[ \"\${$1+x}\" = x ]"; }
env_get() { eval "printf '%s' \"\${$1}\""; }

resolve_value() {
  _key=$1; _default=$2; _req=$3
  if set_has "$_key"; then VAL=$(set_get "$_key"); SRC='set'
  elif env_has "$_key"; then VAL=$(env_get "$_key"); SRC='env'
  elif _v=$(kv_get "$CONF_FILE" "$_key"); then VAL=$_v; SRC='file'
  elif [ -n "$_default" ]; then VAL=$_default; SRC='default'
  elif [ "$_req" = "required" ]; then VAL=''; SRC='missing'; return 1
  else VAL=''; SRC='default'
  fi
  return 0
}

validate_config() {
  _missing=''
  for _pair in $(printf '%s\n' "$SETS" | grep -v '^$' | sed 's/=.*//'); do
    schema_lines | grep -q "^$_pair|" || die "--set で指定された $_pair は、設定スキーマ（$SCHEMA）で宣言されていません"
  done
  schema_lines > "$TMP_SCHEMA"
  while IFS='|' read -r _k _d _r _desc; do
    valid_key "$_k" || die "設定スキーマのキー名が不正です: $_k"
    if resolve_value "$_k" "$_d" "$_r"; then check_value "$_k" "$VAL"; else _missing="$_missing $_k"; fi
  done < "$TMP_SCHEMA"
  [ -z "$_missing" ] || die "必須の設定がありません:$_missing（--set KEY=VALUE か環境変数で指定してください）"
}

write_config() {
  mkdir -p "$CONF_DIR"
  if [ ! -f "$CONF_FILE" ]; then
    (umask 077; printf '# %s\n' "$APP" > "$CONF_FILE")
    log "設定ファイルを作成: $CONF_FILE"
  fi
  chmod 600 "$CONF_FILE"
  schema_lines > "$TMP_SCHEMA"
  while IFS='|' read -r _k _d _r _desc; do
    resolve_value "$_k" "$_d" "$_r" || continue
    case "$SRC" in
      file) continue ;;
    esac
    if grep -q "^$_k=" "$CONF_FILE"; then
      [ "$SRC" = "default" ] && continue
      (umask 077; K=$_k V=$VAL awk '{ if (index($0, ENVIRON["K"] "=") == 1) print ENVIRON["K"] "=\047" ENVIRON["V"] "\047"; else print }' "$CONF_FILE" > "$TMP_CONF")
      cat "$TMP_CONF" > "$CONF_FILE"
      log "設定を更新: $_k（$SRC）"
    else
      printf '\n# %s\n%s='"'"'%s'"'"'\n' "$_desc" "$_k" "$VAL" >> "$CONF_FILE"
      log "設定を追記: $_k（$SRC）"
    fi
  done < "$TMP_SCHEMA"
}

write_state() {
  mkdir -p "$STATE_DIR"
  (umask 022; printf "REF='%s'\nCOMMIT='%s'\nREPO_URL='%s'\nUPDATED_AT='%s'\n" "$REF" "$COMMIT" "$REPO_URL" "$(date -u +%Y-%m-%dT%H:%M:%SZ)" > "$STATE_FILE")
}

install_ctl() {
  if [ -n "$CTL_SUBCOMMAND" ]; then
    [ -x "$CTL" ] || die "app_install が $CTL を置いていません（CTL_SUBCOMMAND を指定したときは、アプリが '$CTL_CMD' を提供します）"
  else
    mkdir -p "$BIN_DIR"
    (umask 022; printf '#!/bin/sh\nexec sh '"'"'%s'"'"' "$@"\n' "$SCRIPT_DIR/main.sh" > "$CTL")
    chmod 755 "$CTL"
  fi
  case ":${PATH:-}:" in
    *":$BIN_DIR:"*) ;;
    *) warn "$BIN_DIR が PATH にありません（$CTL をフルパスで実行してください）" ;;
  esac
}

ref_short() {
  case "$1" in
    refs/heads/*) printf '%s' "${1#refs/heads/}" ;;
    refs/tags/*) printf '%s' "${1#refs/tags/}" ;;
    *) printf '%s' "$1" ;;
  esac
}

ref_kind() {
  case "$1" in
    refs/heads/*) printf 'branch' ;;
    refs/tags/*) printf 'tag' ;;
    *) printf 'unknown' ;;
  esac
}

remote_has() {
  printf '%s\n' "$1" | awk -F'\t' -v r="$2" '$2 == r { f = 1 } END { exit !f }'
}

resolve_ref() {
  _name=$1; _url=$2
  case "$_name" in
    ""|*[!A-Za-z0-9._/-]*) die "ブランチ・タグ名に使えない文字が含まれます: $_name" ;;
  esac
  _out=$(git ls-remote "$_url" "refs/heads/*" "refs/tags/*") || die "取得元に接続できません: $_url"
  case "$_name" in
    refs/heads/?*|refs/tags/?*)
      remote_has "$_out" "$_name" || die "取得元に存在しません: $_name"
      RESOLVED=$_name
      return 0 ;;
  esac
  _b=''; _t=''
  if remote_has "$_out" "refs/heads/$_name"; then _b=1; fi
  if remote_has "$_out" "refs/tags/$_name"; then _t=1; fi
  if [ -n "$_b" ] && [ -n "$_t" ]; then
    die "ブランチとタグの両方に $_name があります。refs/heads/$_name か refs/tags/$_name で指定してください"
  elif [ -n "$_b" ]; then RESOLVED="refs/heads/$_name"
  elif [ -n "$_t" ]; then RESOLVED="refs/tags/$_name"
  else die "取得元にブランチもタグも存在しません: $_name（'$CTL_CMD refs' で一覧を表示できます）"
  fi
}

switch_and_reexec() {
  _url=$REPO_URL
  if [ "$SUB" = "update" ]; then
    [ -f "$STATE_FILE" ] || die "導入されていません（$STATE_FILE がありません）。先に install.sh install を実行してください"
    [ -n "$_url" ] || _url=$(kv_get "$STATE_FILE" REPO_URL) || die "導入メタ情報にREPO_URLがありません"
    [ -n "$REF" ] || REF=$(kv_get "$STATE_FILE" REF) || die "導入メタ情報にREFがありません"
  fi
  [ -d "$DIR/.git" ] || die "$DIR はgitリポジトリではありません"
  resolve_ref "$REF" "$_url"
  if [ -n "$(git -C "$DIR" status --porcelain --untracked-files=no)" ]; then
    die "$DIR に未コミットの変更があります。退避してから再実行してください"
  fi
  log "取得: $(ref_short "$RESOLVED")（$(ref_kind "$RESOLVED")）"
  git -C "$DIR" remote set-url origin "$_url"
  git -C "$DIR" fetch -q --depth 1 origin "$RESOLVED"
  _commit=$(git -C "$DIR" rev-parse 'FETCH_HEAD^{commit}')
  git -C "$DIR" checkout -q --detach FETCH_HEAD
  set --
  _oldifs=$IFS
  IFS='
'
  set -f
  for _l in $SETS; do
    [ -n "$_l" ] || continue
    set -- "$@" --set "$_l"
  done
  set +f
  IFS=$_oldifs
  exec sh "$SCRIPT_DIR/main.sh" "$SUB" --ref "$RESOLVED" --commit "$_commit" --repo-url "$_url" "$@"
}

cmd_apply() {
  check_user
  [ -n "$REF" ] && [ -n "$COMMIT" ] && [ -n "$REPO_URL" ] || die "$SUB は install.sh 経由で実行してください（--ref・--commit・--repo-url が必要です）"
  [ "$SUB" = "install" ] || [ -f "$STATE_FILE" ] || die "導入されていません。先に install.sh install を実行してください"
  validate_config
  write_config
  mkdir -p "$BIN_DIR"
  app_install
  write_state
  install_ctl
  log "完了（$SUB）: $REF（$COMMIT）"
}

cmd_uninstall() {
  check_user
  app_uninstall
  if [ -n "$PURGE" ]; then
    app_purge
    for _d in "$CONF_DIR" "$DATA_DIR" "$STATE_DIR"; do
      safe_to_remove "$_d" || die "削除を中止します: $_d"
    done
    log "設定・データ・導入メタ情報を削除: $CONF_DIR $DATA_DIR $STATE_DIR"
    rm -rf "$CONF_DIR" "$DATA_DIR" "$STATE_DIR"
  fi
  rm -f "$CTL"
  safe_to_remove "$DIR" || die "取得先の削除を中止します: $DIR"
  [ -d "$DIR/.git" ] || die "取得先の削除を中止します（gitリポジトリではありません）: $DIR"
  log "取得先を削除: $DIR"
  cd /
  rm -rf "$DIR"
  if [ -z "$PURGE" ]; then
    log "設定・データは残しています（削除するには uninstall --purge）: $CONF_DIR $DATA_DIR"
  fi
  log "完了（uninstall）"
}

cmd_status() {
  [ -f "$STATE_FILE" ] || { echo "未導入"; return 0; }
  _ref=$(kv_get "$STATE_FILE" REF)
  echo "導入先:     $DIR"
  echo "導入方式:   $INSTALL_MODE"
  echo "参照:       $(ref_short "$_ref")（$(ref_kind "$_ref")）"
  echo "コミット:   $(kv_get "$STATE_FILE" COMMIT)"
  echo "取得元:     $(kv_get "$STATE_FILE" REPO_URL)"
  echo "更新日時:   $(kv_get "$STATE_FILE" UPDATED_AT)"
  echo "設定:       $CONF_FILE"
}

cmd_refs() {
  [ -f "$STATE_FILE" ] || die "導入されていません（$STATE_FILE がありません）"
  _url=$(kv_get "$STATE_FILE" REPO_URL) || die "導入メタ情報にREPO_URLがありません"
  _out=$(git ls-remote "$_url" "refs/heads/*" "refs/tags/*") || die "取得元に接続できません: $_url"
  printf '%s\n' "$_out" | awk -F'\t' '
    $2 ~ /\^\{\}$/ { next }
    $2 ~ /^refs\/heads\// { sub(/^refs\/heads\//, "", $2); print "branch\t" $2 }
    $2 ~ /^refs\/tags\// { sub(/^refs\/tags\//, "", $2); print "tag\t" $2 }
  '
}

main() {
  [ "$#" -ge 1 ] || { usage 1>&2; exit 1; }
  SUB=$1; shift
  REF=''; COMMIT=''; REPO_URL=''; PURGE=''
  case "$SUB" in
    install|update|uninstall|status|refs) ;;
    -h|--help|help) usage; exit 0 ;;
    *) usage 1>&2; exit 1 ;;
  esac

  while [ "$#" -gt 0 ]; do
    case "$1" in
      --ref) [ "$#" -ge 2 ] || die "--ref に値がありません"; REF=$2; shift 2 ;;
      --ref=*) REF=${1#--ref=}; shift ;;
      --commit) [ "$#" -ge 2 ] || die "--commit に値がありません"; COMMIT=$2; shift 2 ;;
      --repo-url) [ "$#" -ge 2 ] || die "--repo-url に値がありません"; REPO_URL=$2; shift 2 ;;
      --set)
        [ "$#" -ge 2 ] || die "--set に値がありません"
        case "$2" in *=*) ;; *) die "--set は KEY=VALUE の形で指定してください: $2" ;; esac
        valid_key "${2%%=*}" || die "--set のキー名が不正です: ${2%%=*}"
        SETS="$SETS
$2"
        shift 2 ;;
      --purge) PURGE=1; shift ;;
      -h|--help) usage; exit 0 ;;
      *) die "不明なオプションです: $1" ;;
    esac
  done
  [ -z "$PURGE" ] || [ "$SUB" = "uninstall" ] || die "--purge は uninstall のオプションです"
  [ -z "$SETS" ] || [ "$SUB" = "install" ] || [ "$SUB" = "update" ] || die "--set は install・update のオプションです"
  [ -z "$REF" ] || [ "$SUB" = "install" ] || [ "$SUB" = "update" ] || die "--ref は install・update のオプションです"

  if [ -z "$COMMIT" ]; then
    if [ "$SUB" = "update" ] || { [ "$SUB" = "install" ] && [ -n "$REF" ]; }; then
      check_user
      switch_and_reexec
    fi
  fi

  TMP_SCHEMA=$(mktemp); TMP_CONF=$(mktemp)
  trap 'rm -f "$TMP_SCHEMA" "$TMP_CONF"' EXIT

  case "$SUB" in
    install|update) cmd_apply ;;
    uninstall) cmd_uninstall ;;
    status) cmd_status ;;
    refs) cmd_refs ;;
  esac
}

main "$@"
exit 0
