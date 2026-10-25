# ytdlp Server

ネットワーク内から yt-dlp を実行する API サーバ。URL を POST すると動画をダウンロードして保存する。

## 導入の流れ

1. （任意）[保存先に NAS を使う](#保存先に-nas-を使う任意)
2. 導入方法を選んで導入する
   - [Docker Compose で使う](#docker-compose-で使う)
   - [Ubuntu (LXC/ベアメタル) で使う](#ubuntu-lxcベアメタル-で使う)
3. [動画をダウンロードする](#動画をダウンロードする)

---

## 保存先に NAS を使う（任意）

動画の保存先に NAS (Samba) を使う場合、導入前に、動画を保存するサーバ（LXC の場合は LXC のホスト）でマウントしておく。  
以降、保存先は `/mnt/video` として記載する。

```sh
sudo apt install cifs-utils

sudo mkdir -p /mnt/video
```

`/etc/fstab` に追記する。

```conf
//192.168.3.120/Videos   /mnt/video   cifs  nofail,_netdev,x-systemd.automount,user=<ユーザ名>,password=<パスワード>,file_mode=0666,dir_mode=0777  0  0
```

認証情報を別ファイルにする場合は、`user=...,password=...` を `credentials=/etc/smb-credentials/.pw` に置き換える。

```sh
sudo mkdir -p /etc/smb-credentials/
cat << EOF | sudo tee /etc/smb-credentials/.pw
username=<ユーザ名>
password=<パスワード>
EOF
sudo chmod 600 /etc/smb-credentials/.pw
```

マウントする。

```sh
sudo mount -a
```

---

## Docker Compose で使う

### 使う構成を選ぶ

用途に応じて、使う compose ファイルを 1 つ選ぶ。以降の手順の `<ファイル>` に、選んだファイル名を指定する。

| 構成              | `<ファイル>`                    | API の URL                          |
| ----------------- | ------------------------------- | ----------------------------------- |
| HTTP（基本）      | `docker-compose.yml`            | `http://<IPアドレス>:5000/download` |
| HTTPS（nginx）    | `docker-compose.nginx.yml`      | `https://<IPアドレス>/download`     |
| Cloudflare Tunnel | `docker-compose.cloudflare.yml` | Cloudflare で設定したホスト名       |

- HTTPS（nginx）は自己署名証明書を使う。
- Cloudflare Tunnel は、ファイル内の `<YOUR_CLOUDFLARE_TOKEN>` をトークンに書き換える。

### 1. Docker をインストールする

```sh
sudo apt update
sudo apt install docker.io
sudo gpasswd --add $USER docker
newgrp docker
```

docker-compose をインストールする。

```sh
sudo curl -SL https://github.com/docker/compose/releases/download/v2.4.1/docker-compose-linux-x86_64 -o /usr/local/bin/docker-compose
sudo chmod +x /usr/local/bin/docker-compose
docker-compose --version
```

### 2. 保存先を設定する

`<ファイル>` の `dispatcher` の `volumes` を編集する（`<ホスト側>:<コンテナ側>`）。worker は、ジョブがあるときだけ dispatcher が起動する（常駐しない）ため、`worker` サービス自体は無い。

```yml
dispatcher:
  volumes:
    - /mnt/video:/download
```

### 3. 起動する

```sh
docker-compose -f <ファイル> up -d --build
```

同時に動く worker の最大数は `WORKER_MAX`（既定 1）で指定する。

```sh
WORKER_MAX=4 docker-compose -f <ファイル> up -d --build
```

### 4. 動作確認する

```sh
docker-compose -f <ファイル> ps
```

全サービスが `Up` になっていることを確認する。  
`405` が返れば API は動作している（HTTPS の場合は URL を `https://...` にし、`-k` を付ける）。

```sh
curl -s -o /dev/null -w "%{http_code}\n" http://<IPアドレス>:5000/download
```

Redis のキューは `http://<IPアドレス>:5540`（Redis Insight）で確認できる。

### 運用

```sh
## ログを見る
docker-compose -f <ファイル> logs -f

## 更新する
git pull
docker-compose -f <ファイル> up -d --build

## worker の最大数を変える
WORKER_MAX=8 docker-compose -f <ファイル> up -d

## 再起動する
docker-compose -f <ファイル> restart

## 停止する
docker-compose -f <ファイル> down
```

---

## Ubuntu (LXC/ベアメタル) で使う

Ubuntu 24.04 に Docker なしでインストールする。インストーラは 1 行の取得実行コマンドで導入でき、導入後は常設コマンド `ytdlpserver-ctl` で更新・撤去・状態確認を行う。

### インストール

#### 1. 実行環境を用意する

メモリは **1GB 以上**を割り当てる（pot-provider のビルドに必要）。ディスクは 4GB 以上を推奨する。ログイン用ブラウザ（既定で有効）を使う場合は、メモリ 2GB 以上・ディスクの空き 1GB 以上を推奨する（[詳細](#ログイン用ブラウザの設定)）。Redis Insight をビルドする場合は、ビルド時のみ追加の要件がある（[Redis の Web UI](#redis-web-uiの設定)を参照）。

**LXC の場合**、コンテナを作成してシェルに入る。

```sh
lxc launch ubuntu:24.04 ytdlp
lxc exec ytdlp -- bash
```

保存先にホストのディレクトリ（[NAS](#保存先に-nas-を使う任意) など）を使う場合は、ホスト側で実行する。

```sh
lxc config device add ytdlp video disk source=/mnt/video path=/mnt/video
## 非特権コンテナで書き込めない場合
lxc config device set ytdlp video shift=true
```

**ベアメタルの場合**、Ubuntu 24.04 に root（または sudo）で操作する。

#### 2. インストールする

コンテナ内（ベアメタルは Ubuntu 上）で実行する。

```sh
curl -fsSL https://github.com/nekono-dev/ytdlpServer/releases/latest/download/install.sh | sudo sh -s -- install
```

設定値は `--set KEY=VALUE` で指定する。指定した値は `/etc/ytdlpserver/ytdlpserver.env`（権限 640・グループ ytdlpserver）に保存される。各サービスは専用ユーザー `ytdlpserver` で動き、動画の保存先（`DOWNLOAD_DIR`）はそのユーザーの所有になる。

```sh
curl -fsSL https://github.com/nekono-dev/ytdlpServer/releases/latest/download/install.sh | sudo sh -s -- install --set WORKER_MAX=4 --set DOWNLOAD_DIR=/mnt/video
```

初回は数分かかる。完了すると API の URL が表示される。`install` は何度実行しても同じ結果になる。

- 値の優先順位は、`--set` > 環境変数 > 既存の設定ファイル > 既定値。既存の設定ファイルの値は、`--set` か環境変数で指定しない限り変更されない。
- 受け付けるのは下表のキーだけ。未宣言のキーや、`'`・改行を含む値はエラーになる（何も変更されない）。
- `--set` の値は `ps` やシェル履歴に残る。`CLOUDFLARE_TOKEN` のような秘匿値は、環境変数（`sudo env CLOUDFLARE_TOKEN=... sh -s -- install ...`）か、設定ファイルへの直接記入で渡す。
- 取得先（ソースの展開先）は環境変数 `YTDLPSERVER_DIR` で変更できる（既定 `/opt/ytdlpserver`、`install` 時のみ）。venv などはこの直下に展開され、`uninstall` で削除される。

| 設定キー | 既定値 | 内容 |
| --- | --- | --- |
| DOWNLOAD_DIR | `/mnt` | 動画の保存先 |
| COOKIE_DIR | `/var/lib/ytdlpserver/cookies` | cookie プロファイルの保存先 |
| WORKER_MAX | `1` | 同時に動く worker の最大数 |
| API_PORT | `5000` | API の待ち受けポート |
| POT_PORT | `4416` | pot-provider の待ち受けポート（127.0.0.1 限定） |
| REDIS_TTL | `604800` | ジョブ情報の保持秒数 |
| RETRY_COUNT | `5` | ダウンロードの再試行回数 |
| YTDLP_REPO | `yt-dlp/yt-dlp` | yt-dlp の取得元 GitHub リポジトリ |
| UPDATE_INTERVAL | `21600` | yt-dlp の新版の確認間隔（秒） |
| UPDATE_RETRY_INTERVAL | `1800` | 確認失敗後の再確認間隔（秒） |
| UPDATE_COOLDOWN | `1800` | probe 失敗による確認依頼の最短間隔（秒） |
| KEEP_VERSIONS | `2` | 導入先に残す yt-dlp の版数 |
| DISPATCH_SCAN_INTERVAL | `30` | dispatcher がジョブを定期確認する間隔（秒） |
| LEASE_TTL | `60` | worker のリースの有効期間（秒） |
| HEARTBEAT_INTERVAL | `10` | worker がリースを更新する間隔（秒） |
| STOP_GRACE | `20` | 停止指示から強制終了までの猶予（秒） |
| INPROGRESS_STALE | `21600` | 所有者不明の処理中ジョブを回収するまでの秒数 |
| WITH_BROWSER | `1` | 1 でログイン用ブラウザを導入する |
| BROWSER_PORT | `8080` | ログイン操作画面のポート |
| BROWSER_UI_URL | なし | login_url の基準 URL（空なら外向き IP から組み立てる） |
| SESSION_TIMEOUT | `900` | ログイン操作の自動終了までの秒数 |
| BROWSER_LANG | `ja,en-US,en` | ブラウザの言語 |
| BROWSER_TZ | `Asia/Tokyo` | ブラウザのタイムゾーン |
| WITH_NGINX | `0` | 1 で nginx（443、自己署名証明書）を導入する |
| SSL_CN | `localhost` | 自己署名証明書の CN |
| WITH_CLOUDFLARED | `0` | 1 で cloudflared を導入する |
| CLOUDFLARE_TOKEN | なし | Cloudflare Tunnel のトークン |
| WITH_REDIS_UI | `1` | 1 で Redis の Web UI を導入する |
| REDIS_UI | `insight` | insight または commander |
| REDIS_INSIGHT_VERSION | `3.8.0` | ビルドする Redis Insight のタグ |
| RI_BUILD_STORAGE | `auto` | ビルド先（tmpfs・disk・auto） |
| REDIS_UI_HOST | `0.0.0.0` | Web UI の待ち受けアドレス |

例: HTTPS を追加し、Redis の Web UI は軽量な commander にする。

```sh
curl -fsSL https://github.com/nekono-dev/ytdlpServer/releases/latest/download/install.sh | sudo sh -s -- install --set WITH_NGINX=1 --set REDIS_UI=commander
```

##### HTTPS（nginx）の設定

`WITH_NGINX=1` で nginx（443）を導入し、HTTPS を有効にする。自己署名証明書を使い、証明書の CN は `SSL_CN=<ホスト名>` で指定する。

API の URL は `https://<IPアドレス>/download` になる。`WITH_NGINX=0` に戻して `update` すると、nginx の設定が撤去され、nginx は停止する。

##### Cloudflare Tunnelの設定

`WITH_CLOUDFLARED=1` と `CLOUDFLARE_TOKEN=<トークン>` で Cloudflare Tunnel を有効にする（トークンは環境変数で渡すとよい）。

- インストール時に、GitHub Releases から cloudflared の最新版を取得し、GitHub API が返すダイジェスト（sha256）と照合してから導入する。

##### Redis Web UIの設定

`WITH_REDIS_UI=1`（既定で有効）で Redis の Web UI を導入する。アクセス先は `http://<IPアドレス>:5540`。`REDIS_UI_HOST` で待ち受けアドレスを変更できる。

- `REDIS_UI=commander` を指定すると、最初から軽量な redis-commander を使う。
- Redis Insight は SSPL ライセンスのため、ビルド済みバイナリは配布しない。インストーラーが公式 GitHub のソース（`REDIS_INSIGHT_VERSION` のタグ）を取得し、その場でビルドする。
  - 要件: メモリ 2GB 以上、初回の所要時間は約 6 分（ビルド済みなら再実行時はスキップ）
  - 要件を満たさない場合やビルドに失敗した場合は、警告を出して redis-commander で継続する
- **ビルド中はピークで約 9.5GB の作業領域を使う**（ソースと `node_modules` 約 3.3GB、yarn キャッシュ約 5.7GB）。作業領域はビルド後にすべて削除され、残るのは約 0.3GB のみ。置き場所は `RI_BUILD_STORAGE` で選ぶ。

| `RI_BUILD_STORAGE` | 作業領域 | 必要な資源 |
| --- | --- | --- |
| `auto`（既定） | メモリが足りれば tmpfs、足りない・マウントできなければ disk | 下記のいずれか |
| `tmpfs` | メモリ上（tmpfs） | **インストール時のみ メモリ 12GB 程度**。ディスクは使わない。ビルド後にメモリは解放される |
| `disk` | ディスク | **空き容量 10GB 程度**（ビルド中のみ） |

- tmpfs をマウントできない環境（権限のない LXC など）では、`auto` は disk にフォールバックし、`tmpfs` を明示した場合は警告を出して commander で継続する。

##### ログイン用ブラウザの設定

`WITH_BROWSER=1`（既定で有効）で、ログインが必要なサイトの cookie を取得するためのブラウザ（Chromium）を導入する。Ubuntu の `chromium` パッケージは snap のため、Playwright が配布する Chromium を `/opt/ytdlpserver/browsers` に導入する。詳しい使い方は[ログインが必要な動画を取得する](#ログインが必要な動画を取得する)を参照。

- 要件: メモリ 2GB 以上・ディスクの空き 1GB 以上を推奨（ログイン中の Chromium はピークで 1.5GB 程度のメモリを使う）。
  満たさない場合は、`WITH_BROWSER` を明示していなければ自動で無効化（警告を出したうえで継続）し、明示している場合は警告のみ出して続行する。
- アクセス先は `http://<IPアドレス>:8080`。`BROWSER_PORT` でポートを変更できる。
- `BROWSER_UI_URL`（401 応答の `login_url` に使う URL）は、サーバの外向き IP から自動で組み立てられる。手動で指定する場合や、nginx・Cloudflare Tunnel 経由で公開する場合は明示する。
- 画面（8080）に認証は無い。LAN 内だけで使い、ポートを外部へ公開しない。Cloudflare Tunnel の対象にも含めない。
- 不要な場合は `WITH_BROWSER=0` で無効化する。

#### 3. 動作確認する

```sh
systemctl status ytdlpserver-api ytdlpserver-dispatcher
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:5000/download
```

`405` が返れば API は動作している。

### 設定を変更・更新する方法

導入後は `ytdlpserver-ctl` を使う（`curl` は不要）。

```sh
## 設定を変えて再適用する（変更する項目だけ指定すればよい）
sudo ytdlpserver-ctl update --set WORKER_MAX=8

## 任意機能の有効・無効を切り替える
sudo ytdlpserver-ctl update --set WITH_NGINX=1
sudo ytdlpserver-ctl update --set WITH_NGINX=0

## 導入中のブランチ・タグの最新へ更新する（設定・cookie・Redis のデータは維持される）
sudo ytdlpserver-ctl update

## 別のブランチ・タグへ切り替える（以後は切り替え先に追従する）
sudo ytdlpserver-ctl update --ref v1.3
ytdlpserver-ctl refs   # 切り替えられるブランチ・タグの一覧

## 導入情報（導入先・参照・コミット・取得元・更新日時）
ytdlpserver-ctl status
```

`/etc/ytdlpserver/ytdlpserver.env` を直接編集してから `sudo ytdlpserver-ctl update` を実行してもよい。

- `ytdlpserver-ctl update` は、導入中のブランチ・タグの最新を取得して再適用する。`install.sh` を使った `update` も同じ動作になる（`install.sh` のコミットへは戻さない）。
- 開発中のブランチを試すときは、`install.sh` に `--ref <ブランチ名>` を付けて導入する（例: `curl -fsSL …/install.sh | sudo sh -s -- install --ref dev/v1.3`）。
- `update` では、設定スキーマに新しく増えたキーが既定値で追記される。既存の値は変わらない。

状態を見る・ログを見る・再起動する。

```sh
systemctl status ytdlpserver-api

journalctl -u ytdlpserver-api -f

systemctl restart ytdlpserver-api ytdlpserver-dispatcher
```

サービス名: `ytdlpserver-pot` / `ytdlpserver-api` / `ytdlpserver-dispatcher`（worker はここから起動される） / `redis-server` / `nginx` / `ytdlpserver-cloudflared` / `ytdlpserver-redis-ui` / `ytdlpserver-browser`

### アンインストールする方法

`uninstall` は、アプリ本体（サービス・常設コマンド・取得先）を削除する。設定（`/etc/ytdlpserver`）・cookie・Redis のデータ（ジョブキュー）・動画の保存先はそのまま残るため、再度 `install` すると設定を引き継げる。

```sh
sudo ytdlpserver-ctl uninstall
```

`--purge` を付けると、設定・cookie・Redis のデータ・導入メタ情報も削除する。**動画の保存先は `--purge` でも削除されない**。

```sh
sudo ytdlpserver-ctl uninstall --purge
```

- apt で導入したパッケージ（redis-server・nginx・ffmpeg など）は削除しない。

---

## 動画をダウンロードする

`/download` に POST する。

```sh
curl -H "Content-Type: application/json" -X POST "http://<IPアドレス>:5000/download" \
  -d '{"url": "https://www.youtube.com/watch?v=XXXXXXXXXX", "options": "--format bv*+ba/best", "savedir": "unsorted"}'
```

HTTPS（nginx）の場合は URL を `https://<IPアドレス>/download` にし、`curl` に `-k` を付ける。

保存先に同じ名前のファイルがあるときは、既存のファイルを上書きせず、名前の末尾に連番を付けて保存する（`動画.mp4` がある場合は `動画 (1).mp4`）。

iOS ショートカットなどを作成すると楽に操作できる。

<details><summary>image</summary>

![iOS Shortcut example](.github/images/image.png)

</details>

### リクエストの項目

| 項目         | 型     | 内容                                                                                                                         |
| ------------ | ------ | ---------------------------------------------------------------------------------------------------------------------------- |
| url          | string | ダウンロードする動画の URL                                                                                                   |
| options      | string | yt-dlp のオプション                                                                                                          |
| savedir      | string | 保存先のサブディレクトリ（指定した場合、作成して保存する）                                                                   |
| namefield    | string | 保存ファイル名のテンプレート（例: `%(title)s-%(id)s`）                                                                       |
| auth_profile | string | 使う cookie プロファイル名。省略すると URL のサイトから自動で選ぶ（[ログインが必要な動画](#ログインが必要な動画を取得する)） |

`namefield` は yt-dlp の `%(key)s` 形式で指定する。拡張子は自動で付く。

### オプションの例

`options` には yt-dlp のオプションを指定する。

| やりたいこと                     | オプション                                                                              |
| -------------------------------- | --------------------------------------------------------------------------------------- |
| YouTube の音声を日本語にする     | `-f "bestaudio[ext=m4a][language^=ja]"`                                                 |
| タイトル等を日本語の翻訳にする   | `--extractor-args youtube:lang=ja`（音声は変わらない）                                  |
| ファイル名の文字化けを防ぐ       | `--windows-filenames`                                                                   |
| ログインして取得する             | `options` ではなく cookie プロファイルを使う（[後述](#ログインが必要な動画を取得する)） |
| 出力形式を mp4 にする            | `--merge-output-format mp4`                                                             |
| コーデックを avc1 (mp4) にする   | `-f "bestvideo[vcodec^=avc1][ext=mp4]"`                                                 |
| 同じファイルを再ダウンロードする | `--force-overwrites`                                                                    |

---

## ログインが必要な動画を取得する

一部のサイトは、ユーザ名とパスワードでは yt-dlp からログインできない。
このサーバは、ブラウザでログインしたときの **cookie** を、サイトごとの **プロファイル** として保存しておき、取得に使う。

1. ブラウザでログインして、プロファイルを保存する（[下記](#ブラウザでログインして-cookie-を保存する)）。
2. いつもどおり `/download` に URL を送る。URL のサイトに対応するプロファイルが保存されていれば、自動で使われる。

| プリセット（最初から選べるサイト） | プロファイル名 | 対象のドメイン                    |
| ---------------------------------- | -------------- | --------------------------------- |
| YouTube                            | `youtube`      | youtube.com, youtu.be, google.com |
| ニコニコ                           | `niconico`     | nicovideo.jp, nico.ms             |
| Instagram                          | `instagram`    | instagram.com                     |
| X (Twitter)                        | `x`            | x.com, twitter.com                |
| Bilibili                           | `bilibili`     | bilibili.com, b23.tv              |

それ以外のサイトも、ログイン画面で「新しいサイトを追加」から使える。一度保存すると、次回からプルダウンで選べ、自動選択の対象にもなる。

- 自動で選んだプロファイルの cookie が未保存・失効中でも、ログイン不要の動画は取得できる。
- 使うプロファイルを明示したい場合は、リクエストに `auth_profile` を指定する（自動選択より優先）。`/schedule` でも指定できる。

```sh
curl -H "Content-Type: application/json" -X POST "http://<IPアドレス>:5000/download" \
  -d '{"url": "https://www.nicovideo.jp/watch/sm00000000", "auth_profile": "niconico"}'
```

- `options` の `-u` / `-p`（`--username` / `--password`）、`--cookies`、`--netrc*` は使えない（400 になる）。
- cookie は `<プロファイル名>.txt` として保存される。保存先は Docker Compose では `cookies/`、Ubuntu インストーラでは `/var/lib/ytdlpserver/cookies/`（`COOKIE_DIR` で変更可）。
  cookie はパスワードと同じ扱いにし、他人に渡さず、Git にもコミットしない（`cookies/` は `.gitignore` 済み）。
- ログインには、普段使いとは別のアカウントを使うことを勧める（サイトによっては、別環境での cookie の利用でアカウントが保護・制限されることがある）。

### ブラウザでログインして cookie を保存する

ブラウザでログインするだけで、cookie がプロファイルとして保存される。cookies.txt を書き出す必要はない。
スマートフォンでも PC でも使える。サーバ上のブラウザの画面が、この端末の画面の大きさで表示される。

Ubuntu インストーラは既定でこの機能を導入し（[詳細](#ログイン用ブラウザの設定)）、`BROWSER_UI_URL` もサーバの IP から自動で設定される。Docker Compose の場合は、下記の手順が必要。

1. （推奨・Docker Compose のみ）`.env` に `BROWSER_UI_URL=http://<サーバのIPアドレス>:8080` を書いて、起動し直す。
   401 の `login_url` から、この画面を開けるようになる。
2. ブラウザで `http://<サーバのIPアドレス>:8080/` を開く（`login_url` を開くと、該当するプロファイルが選ばれた状態で開く）。
3. プロファイル（サイト）をプルダウンから選び、「ログイン用ブラウザを開く」を押す。
   - 一覧に無いサイトは「＋ 新しいサイトを追加…」を選び、プロファイル名（英数字・`-`・`_`、64 文字まで）とログイン画面の URL を入力する。
   - プルダウンの ✓ は保存済み。選ぶと保存日時が表示される。
4. 表示された画面でログインする。CAPTCHA や 2 段階認証もこの画面で操作する。
   - 入力欄をタップすると、端末のキーボードが出る（⌨ でも開閉できる）。
   - パスワードマネージャの値は「貼付」から送る（入力欄をタップしてから、貼り付けて「送信」）。
   - ← は戻る、↻ は再読み込み。外部アカウントでのログインなどで新しい画面が開くと、表示が切り替わる。
5. ログインが終わったら「保存」を押す。`cookies/<プロファイル名>.txt` に保存される。

- 保存されるのは、そのサイトの対象ドメインの cookie だけ（追加したサイトは、入力した URL の登録ドメイン配下）。同じプロファイルで保存し直すと上書きされる（再ログイン）。
- 追加したサイトは「削除」で消せる（保存した cookie も消える）。URL を変えたい場合は、削除して追加し直す。プリセットは削除できない。
- 同時に使えるのは 1 件。`SESSION_TIMEOUT`（秒、既定 900）で自動終了する。保存・中止・時間切れのあと、ブラウザとその履歴は破棄される。
- 画面（8080）に認証は無い。LAN 内だけで使い、ポートを外部へ公開しない。Cloudflare Tunnel の対象にも含めない。

### プロファイルの一覧

```sh
curl "http://<IPアドレス>:5000/auth/profiles"
```

cookie を保存済みのプロファイルの `profile`（名前）・`label`（表示名）・`domains`・`status`・`updated_at`・`login_url` を返す。`status` は `valid`（有効）か `expired`（失効を検知済み）。cookie の中身は返さない。

### ログインが必要な場合の応答

cookie が未指定・未登録・失効しているときは、リクエストの時点で **HTTP 401** が返る。

```json
{
  "error": "login_required",
  "reason": "cookie_expired",
  "auth_profile": "niconico",
  "message": "プロファイル 'niconico' の cookie が無効です。再ログインして cookie を更新してください。",
  "login_url": "http://<サーバのIPアドレス>:8080/?profile=niconico&start_url=https%3A%2F%2Fwww.nicovideo.jp%2F"
}
```

| 項目         | 内容                                                                    |
| ------------ | ----------------------------------------------------------------------- |
| reason       | 下表                                                                    |
| auth_profile | 対象のプロファイル（明示指定、または自動で選んだもの）。無ければ `null` |
| login_url    | 再ログイン用の画面の URL。設定されていなければ `null`                   |

| reason            | 意味                                                                         |
| ----------------- | ---------------------------------------------------------------------------- |
| `cookie_missing`  | ログインが必要だが、対応するプロファイルが無い（新しいサイトとして追加する） |
| `profile_unknown` | プロファイルの cookie が保存されていない                                     |
| `cookie_expired`  | cookie が失効している                                                        |

- cookie を更新すると、サーバを再起動しなくても `valid` に戻る。
- キューに積んだ後で cookie が失効した場合は、ジョブが失敗し、ジョブ情報に `error_code=login_required` が入る。cookie を更新するまで自動リトライはしない。
- `/download/scheduled` で 401 になった予約は、削除されずに残る。それ以外の理由で失敗した予約は、Redis の `ytdlp:requests:failed` に退避され、残りの予約は処理される（応答の `failed` が退避した件数）。

---

## トラブルシューティング

### `login_required`（HTTP 401）が返る

プロファイルの cookie が未保存または失効している。[ログインが必要な場合の応答](#ログインが必要な場合の応答)の `login_url` を開き、ログインして保存し直す。

### X のログインで `We've temporarily limited your login. Please try again later.` と表示され、パスワード欄が出ない

X 側が、このサーバのブラウザからのログインを制限している。ログイン画面で、実在するユーザ名を送ったときだけ出る（存在しないユーザ名では出ない）。

原因は、**ブラウザのタイムゾーンが、サーバの外向き IP の所在地と食い違っていること**（UTC のまま、IP は日本、など）。次のように `TZ` を IP の所在地に合わせて、`browser` を作り直す。

```sh
# .env（compose ファイルと同じ場所）
TZ=Asia/Tokyo
BROWSER_LANG=ja,en-US,en
```

```sh
docker compose up -d --build browser
```

- 既定は `Asia/Tokyo`（日本語）。海外のサーバや VPN 経由で出ている場合は、その所在地のタイムゾーン（例: `America/New_York`）と言語（例: `en-US,en`）にする。
- `TZ` が UTC のままだと、`browser` のログに `WARNING: TZ is UTC` が出る。
- 制限された直後は、設定を直しても、しばらく同じ表示になることがある。試行を繰り返さず、時間をあけて試す。

### `yt-dlp probe failed; requested a yt-dlp update check.` が返る

yt-dlp が対応していない URL・動画である可能性がある。しばらく待って、もう一度リクエストする（dispatcher が新版の確認を行う。新版があれば自動で切り替わる）。繰り返す場合は URL と `options` を確認する。

### `この動画はご覧いただけません` になる（YouTube）

もう一度リクエストする（yt-dlp の新版が自動で使われる）。改善しない場合は pot-provider を再起動する。

```sh
## Docker Compose
docker-compose -f <ファイル> restart pot-provider
## Ubuntu
systemctl restart ytdlpserver-pot
```

### `client and server have different API versions`（Docker Compose）

`/etc/docker/daemon.json` に追記して、Docker を再起動する。

```diff
  {
    ...
+   "min-api-version": "1.32"
    ...
  }
```

### インストーラが途中で失敗する（Ubuntu）

- `github.com` に接続できるか確認する。
- もう一度 `install` を実行する（途中からやり直せる）。
- サービスが起動しない場合は `journalctl -u ytdlpserver-<サービス名>` を確認する。
