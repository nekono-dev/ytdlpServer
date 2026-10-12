#!/bin/sh
set -eu
cd "$RI_WORK"
export XDG_CACHE_HOME="$RI_WORK/.cache" YARN_CACHE_FOLDER="$RI_WORK/.cache/yarn" npm_config_cache="$RI_WORK/.cache/npm"
curl -fsSL "https://github.com/RedisInsight/RedisInsight/archive/refs/tags/$RI_VER.tar.gz" | tar xz --strip-components=1
if [ -f yarn.lock ]; then
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
  npm ci --ignore-scripts
  npx patch-package
  npm ci --prefix redisinsight/api
  npm run build:ui
  npm run build:statics
  npm run build:api
  npm ci --prefix redisinsight/api --omit=dev
fi
[ -f redisinsight/api/dist/src/main.js ] && [ -d redisinsight/ui/dist ]
rm -rf "$RI_DEST"
mkdir -p "$RI_DEST/api" "$RI_DEST/ui"
cp -r redisinsight/api/dist redisinsight/api/node_modules "$RI_DEST/api/"
cp -r redisinsight/ui/dist "$RI_DEST/ui/"
echo "$RI_VER" > "$RI_DEST/.version"
