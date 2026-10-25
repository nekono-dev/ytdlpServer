#!/bin/sh
# 使い捨ての Ubuntu 24.04 LXC コンテナでインストーラの受け入れ条件を検証する。
# 使い方: sh tests/installer/e2e.sh <ソースのgitリポジトリ(ブランチtest)> <結果ファイル>
# 環境変数: LXC (lxc コマンド。既定 lxc)、E2E_DNS (コンテナの DNS を固定する場合に指定)、E2E_KEEP=1 (コンテナを残す)
set -u

SRC=$1
OUT=$2
LXC=${LXC:-lxc}
CT=ytdlp-e2e
: > "$OUT"

c() { $LXC exec "$CT" -- sh -c "$1"; }
pass() { echo "$1 PASS" >> "$OUT"; }
fail() { echo "$1 FAIL $2" >> "$OUT"; }
check() { if c "$3" >/dev/null 2>&1; then pass "$1"; else fail "$1" "$2"; fi; }
cleanup() { [ "${E2E_KEEP:-0}" = 1 ] || $LXC delete -f "$CT" >/dev/null 2>&1; }
trap cleanup EXIT

$LXC delete -f "$CT" >/dev/null 2>&1
$LXC launch ubuntu:24.04 "$CT" -c limits.memory=4GiB -c limits.cpu=4 >/dev/null || exit 1
c 'cloud-init status --wait' >/dev/null 2>&1
if [ -n "${E2E_DNS:-}" ]; then
  c "printf 'network:\n  version: 2\n  ethernets:\n    eth0:\n      dhcp4: true\n      dhcp4-overrides:\n        use-dns: false\n      nameservers:\n        addresses: [$E2E_DNS]\n' > /etc/netplan/99-dns.yaml; chmod 600 /etc/netplan/99-dns.yaml; netplan apply; sleep 3"
fi
c 'echo "precedence ::ffff:0:0/96 100" >> /etc/gai.conf'

COMMIT=$(git -C "$SRC" rev-parse HEAD)
BUILD=$(mktemp)
sh "$SRC/installer/build-bootstrap.sh" refs/heads/test "$COMMIT" file:///srv/ytdlp-src > "$BUILD"
c 'mkdir -p /srv'
$LXC file push -r -p "$SRC" "$CT/srv/" >/dev/null
c "mv /srv/$(basename "$SRC") /srv/ytdlp-src; git config --global --add safe.directory '*' 2>/dev/null || true"
$LXC file push "$BUILD" "$CT/root/install.sh" >/dev/null
BAD=$(mktemp)
sh "$SRC/installer/build-bootstrap.sh" refs/heads/test 0000000000000000000000000000000000000000 file:///srv/ytdlp-src > "$BAD"
$LXC file push "$BAD" "$CT/root/install-bad.sh" >/dev/null
rm -f "$BUILD" "$BAD"
c 'apt-get update -qq && apt-get install -y -qq git ca-certificates >/dev/null 2>&1'

c 'sh /srv/ytdlp-src/installer/main.sh status' 2>/dev/null | grep -q '未導入' && pass AC-11a || fail AC-11a "未導入の表示なし"

c 'sh /root/install.sh install --set NO_SUCH_KEY=1' >/dev/null 2>&1; rc1=$?
c "sh /root/install.sh install --set 'WORKER_MAX=1'\"'\"'x'" >/dev/null 2>&1; rc2=$?
if [ "$rc1" = 1 ] && [ "$rc2" = 1 ] && ! c 'test -e /etc/ytdlpserver || test -e /usr/local/bin/ytdlpserver-ctl' >/dev/null 2>&1; then
  pass AC-3
else
  fail AC-3 "rc=$rc1,$rc2"
fi

c 'rm -rf /etc/ytdlpserver /var/lib/ytdlpserver-installer /usr/local/bin/ytdlpserver-ctl /opt/ytdlpserver'

c 'sh /root/install-bad.sh install' >/dev/null 2>&1; rc=$?
if [ "$rc" != 0 ] && ! c 'test -e /etc/ytdlpserver' >/dev/null 2>&1; then pass AC-14; else fail AC-14 "rc=$rc"; fi

c 'sh /root/install.sh install --set WORKER_MAX=abc' >/dev/null 2>&1; rc=$?
if [ "$rc" = 1 ] && ! c 'dpkg -s redis-server' >/dev/null 2>&1; then pass AC-4; else fail AC-4 "rc=$rc"; fi

c 'sh /root/install.sh install --set DOWNLOAD_DIR=/var/lib/ytdlpserver/dl' >/dev/null 2>&1; rc=$?
if [ "$rc" = 1 ] && ! c 'dpkg -s redis-server' >/dev/null 2>&1; then pass AC-5; else fail AC-5 "rc=$rc"; fi

c 'rm -rf /etc/ytdlpserver /var/lib/ytdlpserver-installer /usr/local/bin/ytdlpserver-ctl /opt/ytdlpserver'

t0=$(date +%s)
c 'sh /root/install.sh install --set WITH_BROWSER=0 --set WITH_REDIS_UI=0 > /root/install1.log 2>&1'; rc=$?
t1=$(date +%s)
elapsed=$((t1 - t0))
if [ "$rc" = 0 ] && c 'curl -s -m5 --retry 15 --retry-delay 2 --retry-all-errors -o /dev/null -w "%{http_code}" http://127.0.0.1:5000/ | grep -q 404' >/dev/null 2>&1; then
  pass AC-1
else
  fail AC-1 "rc=$rc"
fi
if [ "$elapsed" -le 900 ]; then pass AC-18; else fail AC-18 "${elapsed}秒"; fi

c 'mkdir -p /var/lib/ytdlpserver/cookies /mnt && echo c > /var/lib/ytdlpserver/cookies/marker && echo v > /mnt/video.mp4 && redis-cli set k v >/dev/null && redis-cli save >/dev/null'
check AC-16 "権限" 'test "$(stat -c %a /etc/ytdlpserver/ytdlpserver.env)" = 600 && test "$(stat -c %a /var/lib/ytdlpserver/cookies)" = 700'
check AC-12a "待ち受け" 'for i in $(seq 1 15); do ss -ltn | grep -q "127.0.0.1:6379" && ss -ltn | grep -q "127.0.0.1:4416" && break; sleep 2; done; ss -ltn | grep -q "127.0.0.1:4416" && ! ss -ltn | grep -E ":(6379|4416) " | grep -v 127.0.0.1'
c 'kill -9 $(systemctl show -p MainPID --value ytdlpserver-api)' >/dev/null 2>&1
check AC-12b "再起動" 'sleep 5; systemctl is-active ytdlpserver-api'

c 'sed -i "s/^API_PORT=.*/API_PORT='"'"'5001'"'"'/" /etc/ytdlpserver/ytdlpserver.env'
c 'sh /root/install.sh install --set WORKER_MAX=2 >/dev/null 2>&1'
check AC-2 "値の保持" 'grep -q "^API_PORT=.5001." /etc/ytdlpserver/ytdlpserver.env && grep -q "^WORKER_MAX=.2." /etc/ytdlpserver/ytdlpserver.env'
c 'sed -i "s/^API_PORT=.*/API_PORT='"'"'5000'"'"'/" /etc/ytdlpserver/ytdlpserver.env'

before=$(c 'stat -c %Y /opt/ytdlpserver/pot-provider/server/build/main.js')
t0=$(date +%s)
c 'sh /root/install.sh install >/dev/null 2>&1'; rc=$?
t1=$(date +%s)
after=$(c 'stat -c %Y /opt/ytdlpserver/pot-provider/server/build/main.js')
if [ "$rc" = 0 ] && [ "$before" = "$after" ] && [ $((t1 - t0)) -le 120 ]; then pass AC-15; else fail AC-15 "rc=$rc $((t1 - t0))秒"; fi

c 'cd /srv/ytdlp-src && echo "NEW_KEY|abc|optional|検証用の新キー" >> installer/config.schema && git -c user.name=t -c user.email=t@t commit -qam new && true'
c 'ytdlpserver-ctl update >/dev/null 2>&1'
check AC-6 "新キー" 'grep -q "^NEW_KEY=.abc." /etc/ytdlpserver/ytdlpserver.env && grep -q "^WORKER_MAX=.2." /etc/ytdlpserver/ytdlpserver.env'

c 'ytdlpserver-ctl update --set WITH_NGINX=1 >/dev/null 2>&1'
if c 'curl -sk -m5 --retry 15 --retry-delay 2 --retry-all-errors -o /dev/null -w "%{http_code}" https://127.0.0.1/ | grep -q 404' >/dev/null 2>&1; then on=1; else on=0; fi
c 'ytdlpserver-ctl update --set WITH_NGINX=0 >/dev/null 2>&1'
if [ "$on" = 1 ] && ! c 'test -e /etc/nginx/conf.d/ytdlpserver.conf || ss -ltn | grep -q ":443 "' >/dev/null 2>&1; then pass AC-7; else fail AC-7 "on=$on"; fi

c 'ytdlpserver-ctl update --set WITH_REDIS_UI=1 --set RI_BUILD_STORAGE=tmpfs > /root/ui.log 2>&1'
check AC-8 "commander" 'grep -q redis-commander /etc/systemd/system/ytdlpserver-redis-ui.service && sleep 3 && ss -ltn | grep -q ":5540 "'

n=$(c 'ytdlpserver-ctl status' 2>/dev/null | grep -c -E '^(導入先|参照|コミット|取得元|更新日時):')
[ "$n" = 5 ] && pass AC-11b || fail AC-11b "項目数=$n"

c 'ytdlpserver-ctl uninstall >/dev/null 2>&1'
check AC-9 "撤去と保持" '! ls /etc/systemd/system/ytdlpserver-*.service >/dev/null 2>&1 && ! test -e /usr/local/bin/ytdlpserver-ctl && ! test -e /opt/ytdlpserver && test -f /etc/ytdlpserver/ytdlpserver.env && test -f /var/lib/ytdlpserver/cookies/marker && test -f /mnt/video.mp4'

c 'sh /root/install.sh install --set WITH_BROWSER=0 --set WITH_REDIS_UI=0 >/dev/null 2>&1'
c 'redis-cli set k v >/dev/null; redis-cli save >/dev/null'
c 'ytdlpserver-ctl uninstall --purge >/dev/null 2>&1'
check AC-10 "purge" '! test -e /etc/ytdlpserver && ! test -e /var/lib/ytdlpserver && ! test -e /var/lib/redis/dump.rdb && test -f /mnt/video.mp4'

echo DONE >> "$OUT"
