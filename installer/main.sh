#!/bin/sh

set -eu

APP='ytdlpserver'
DATA_DIR='/var/lib/ytdlpserver'
CONF_DIR="/etc/$APP"
CONF_FILE="$CONF_DIR/$APP.env"
STATE_DIR="/var/lib/$APP-installer"
STATE_FILE="$STATE_DIR/state"
CTL="/usr/local/bin/$APP-ctl"
SCRIPT_DIR=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)
DIR=$(dirname -- "$SCRIPT_DIR")
SCHEMA="$SCRIPT_DIR/config.schema"

log() { printf '==> %s\n' "$*"; }
die() { printf 'エラー: %s\n' "$*" 1>&2; exit 1; }
usage() {
  cat <<EOF
使い方: $APP-ctl <update|uninstall|status> [オプション]
  update    [--set KEY=VALUE ...]   導入時のREFの最新へ追従して再適用する
  uninstall [--purge]               アプリ本体を削除する。--purge で設定・データ・導入メタ情報も削除する
  status                            導入情報を表示する
EOF
}

app_install() {
  . "$SCRIPT_DIR/app.sh"
  app_run_install
}

app_uninstall() {
  . "$SCRIPT_DIR/app.sh"
  app_run_uninstall
}

app_purge() {
  . "$SCRIPT_DIR/app.sh"
  app_run_purge
}

need_root() { [ "$(id -u)" -eq 0 ] || die "root権限で実行してください"; }

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
  (umask 022; printf '#!/bin/sh\nexec sh '"'"'%s'"'"' "$@"\n' "$SCRIPT_DIR/main.sh" > "$CTL")
  chmod 755 "$CTL"
}

fetch_latest_and_reexec() {
  [ -f "$STATE_FILE" ] || die "導入されていません（$STATE_FILE がありません）。先に install.sh install を実行してください"
  _ref=$(kv_get "$STATE_FILE" REF) || die "導入メタ情報にREFがありません"
  _url=$(kv_get "$STATE_FILE" REPO_URL) || die "導入メタ情報にREPO_URLがありません"
  [ -d "$DIR/.git" ] || die "$DIR はgitリポジトリではありません"
  if [ -n "$(git -C "$DIR" status --porcelain --untracked-files=no)" ]; then
    die "$DIR に未コミットの変更があります。退避してから再実行してください"
  fi
  log "最新を取得: $_ref"
  git -C "$DIR" remote set-url origin "$_url"
  git -C "$DIR" fetch -q --depth 1 origin "$_ref"
  _commit=$(git -C "$DIR" rev-parse 'FETCH_HEAD^{commit}')
  git -C "$DIR" checkout -q --detach FETCH_HEAD
  exec sh "$SCRIPT_DIR/main.sh" update --ref "$_ref" --commit "$_commit" --repo-url "$_url" "$@"
}

cmd_apply() {
  need_root
  [ -n "$REF" ] && [ -n "$COMMIT" ] && [ -n "$REPO_URL" ] || die "$SUB は install.sh 経由で実行してください（--ref・--commit・--repo-url が必要です）"
  [ "$SUB" = "install" ] || [ -f "$STATE_FILE" ] || die "導入されていません。先に install.sh install を実行してください"
  validate_config
  write_config
  write_state
  install_ctl
  app_install
  log "完了（$SUB）: $REF（$COMMIT）"
}

cmd_uninstall() {
  need_root
  app_uninstall
  if [ -n "$PURGE" ]; then
    app_purge
    log "設定・データ・導入メタ情報を削除: $CONF_DIR $DATA_DIR $STATE_DIR"
    rm -rf "$CONF_DIR" "$DATA_DIR" "$STATE_DIR"
  fi
  rm -f "$CTL"
  case "$DIR" in
    ""|/|/usr|/etc|/var|/opt|/home|/root) die "取得先の削除を中止します: $DIR" ;;
  esac
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
  echo "導入先:     $DIR"
  echo "REF:        $(kv_get "$STATE_FILE" REF)"
  echo "コミット:   $(kv_get "$STATE_FILE" COMMIT)"
  echo "取得元:     $(kv_get "$STATE_FILE" REPO_URL)"
  echo "更新日時:   $(kv_get "$STATE_FILE" UPDATED_AT)"
  echo "設定:       $CONF_FILE"
}

main() {
  [ "$#" -ge 1 ] || { usage 1>&2; exit 1; }
  SUB=$1; shift
  REF=''; COMMIT=''; REPO_URL=''; PURGE=''
  case "$SUB" in
    install|update|uninstall|status) ;;
    -h|--help|help) usage; exit 0 ;;
    *) usage 1>&2; exit 1 ;;
  esac

  if [ "$SUB" = "update" ]; then
    _has_commit=''
    for _a in "$@"; do [ "$_a" = "--commit" ] && _has_commit=1; done
    if [ -z "$_has_commit" ]; then need_root; fetch_latest_and_reexec "$@"; fi
  fi

  while [ "$#" -gt 0 ]; do
    case "$1" in
      --ref) [ "$#" -ge 2 ] || die "--ref に値がありません"; REF=$2; shift 2 ;;
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

  TMP_SCHEMA=$(mktemp); TMP_CONF=$(mktemp)
  trap 'rm -f "$TMP_SCHEMA" "$TMP_CONF"' EXIT

  case "$SUB" in
    install|update) cmd_apply ;;
    uninstall) cmd_uninstall ;;
    status) cmd_status ;;
  esac
}

main "$@"
exit 0
