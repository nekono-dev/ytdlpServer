#!/bin/sh
# 責務: 頒布するインストーラ（`install.sh`）を、ひな形（install/{{TEMPLATE_FILENAME}}）へ REF・COMMIT・REPO_URL を埋め込んで標準出力へ出す。
# CI（.github/workflows/installer.yml）が、ブランチ・タグごとに実行する。手元でも同じ手順で試せる。
# 使い方: sh install/build-install.sh <REF> <COMMIT（40桁の16進）> <REPO_URL（https://・file://）> > install.sh
# 失敗時: 引数の形式が不正（置換に使えない文字・桁数違い）、または置換漏れがあれば、何も出力せず終了コード1。
# 例: sh install/build-install.sh v1.0.0 0123456789abcdef0123456789abcdef01234567 https://example.test/repo.git > install.sh

set -eu

[ "$#" -eq 3 ] || { echo "使い方: sh $0 <REF> <COMMIT> <REPO_URL>" 1>&2; exit 1; }
REF=$1
COMMIT=$2
REPO_URL=$3

# シェルの単一引用符と、sedの置換に安全に埋め込める文字だけを許す。
case "$REF" in
  ""|*[!A-Za-z0-9._/-]*) echo "不正なREFです: $REF" 1>&2; exit 1 ;;
esac
case "$COMMIT" in
  *[!0-9a-f]*) echo "COMMITは40桁の16進数（小文字）にしてください: $COMMIT" 1>&2; exit 1 ;;
esac
[ "${#COMMIT}" -eq 40 ] || { echo "COMMITは40桁の16進数にしてください: $COMMIT" 1>&2; exit 1; }
case "$REPO_URL" in
  https://*|file://*) ;;
  *) echo "REPO_URLは https:// か file:// で始めてください: $REPO_URL" 1>&2; exit 1 ;;
esac
case "$REPO_URL" in
  *[!A-Za-z0-9._/:@~+-]*) echo "不正なREPO_URLです: $REPO_URL" 1>&2; exit 1 ;;
esac

SCRIPT_DIR=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)
OUT=$(sed "s|@@REF@@|$REF|; s|@@COMMIT@@|$COMMIT|; s|@@REPO_URL@@|$REPO_URL|" "$SCRIPT_DIR/{{TEMPLATE_FILENAME}}")

# 置換漏れの検査（ひな形の説明文にある文字列は除き、代入行だけを見る）。
if printf '%s\n' "$OUT" | grep -q "^\(REF\|COMMIT\|REPO_URL\)='@@"; then
  echo "置換漏れがあります" 1>&2
  exit 1
fi
printf '%s\n' "$OUT"
