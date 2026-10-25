#!/bin/sh
# 責務: 頒布するインストーラ（install.sh）を、ひな形（installer/bootstrap.sh.tmpl）へ REF（参照の完全名）・COMMIT・REPO_URL と、
#       installer/app.conf の APP_NAME・INSTALL_MODE・CTL_NAME・CTL_SUBCOMMAND を埋め込んで標準出力へ出す。
# CI（.github/workflows/release-installer.yml）が、ブランチ・タグごとに実行する。手元でも同じ手順で試せる。
# 使い方: sh installer/build-bootstrap.sh <REF（refs/heads/<名前> か refs/tags/<名前>）> <COMMIT（40桁の16進）> <REPO_URL（https://・file://）> > install.sh
# 失敗時: 引数・app.conf の形式が不正、または置換漏れがあれば、何も出力せず終了コード1。

set -eu

[ "$#" -eq 3 ] || { echo "使い方: sh $0 <REF> <COMMIT> <REPO_URL>" 1>&2; exit 1; }
REF=$1
COMMIT=$2
REPO_URL=$3

fail() { echo "$*" 1>&2; exit 1; }

# シェルの単一引用符と、sedの置換に安全に埋め込める文字だけを許す。
case "$REF" in
  refs/heads/?*|refs/tags/?*) ;;
  *) fail "REFは refs/heads/<名前> か refs/tags/<名前> の形にしてください: $REF" ;;
esac
case "$REF" in
  *[!A-Za-z0-9._/-]*) fail "不正なREFです: $REF" ;;
esac
case "$COMMIT" in
  *[!0-9a-f]*) fail "COMMITは40桁の16進数（小文字）にしてください: $COMMIT" ;;
esac
[ "${#COMMIT}" -eq 40 ] || fail "COMMITは40桁の16進数にしてください: $COMMIT"
case "$REPO_URL" in
  https://*|file://*) ;;
  *) fail "REPO_URLは https:// か file:// で始めてください: $REPO_URL" ;;
esac
case "$REPO_URL" in
  *[!A-Za-z0-9._/:@~+-]*) fail "不正なREPO_URLです: $REPO_URL" ;;
esac

SCRIPT_DIR=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)
CONF="$SCRIPT_DIR/app.conf"
[ -f "$CONF" ] || fail "アプリ定義がありません: $CONF"

# app.conf はシェルとして読まれるが、ここでは評価せず、KEY=値 か KEY='値' の行だけを読む（CIの環境変数で値が変わらないように）。
conf_get() {
  sed -n "s/^$1='\\([^']*\\)'\$/\\1/p; t; s/^$1=\\([A-Za-z0-9._-]*\\)\$/\\1/p" "$CONF" | tail -n 1
}

valid_name() {
  case "$1" in
    ""|[!a-z]*|*[!a-z0-9-]*) return 1 ;;
  esac
}

APP_NAME=$(conf_get APP_NAME)
INSTALL_MODE=$(conf_get INSTALL_MODE)
CTL_NAME=$(conf_get CTL_NAME)
CTL_SUBCOMMAND=$(conf_get CTL_SUBCOMMAND)
[ -n "$INSTALL_MODE" ] || INSTALL_MODE=system
[ -n "$CTL_NAME" ] || CTL_NAME="$APP_NAME-ctl"
valid_name "$APP_NAME" || fail "app.conf の APP_NAME が不正です（英小文字・数字・-、英小文字始まり）: $APP_NAME"
valid_name "$CTL_NAME" || fail "app.conf の CTL_NAME が不正です: $CTL_NAME"
[ -z "$CTL_SUBCOMMAND" ] || valid_name "$CTL_SUBCOMMAND" || fail "app.conf の CTL_SUBCOMMAND が不正です: $CTL_SUBCOMMAND"
case "$INSTALL_MODE" in
  system|user) ;;
  *) fail "app.conf の INSTALL_MODE は system か user にしてください: $INSTALL_MODE" ;;
esac
DIR_ENV="$(printf '%s' "$APP_NAME" | tr 'a-z-' 'A-Z_')_DIR"

OUT=$(sed "s|@@REF@@|$REF|; s|@@COMMIT@@|$COMMIT|; s|@@REPO_URL@@|$REPO_URL|; s|@@APP_NAME@@|$APP_NAME|; s|@@INSTALL_MODE@@|$INSTALL_MODE|; s|@@CTL_NAME@@|$CTL_NAME|; s|@@CTL_SUBCOMMAND@@|$CTL_SUBCOMMAND|; s|@@DIR_ENV@@|$DIR_ENV|" "$SCRIPT_DIR/bootstrap.sh.tmpl")

# 置換漏れの検査（代入行だけを見る）。
if printf '%s\n' "$OUT" | grep -q "^[A-Z_]*='@@"; then
  fail "置換漏れがあります"
fi
printf '%s\n' "$OUT"
