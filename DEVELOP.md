# 開発ガイド

ytdlpServer の開発者向けドキュメント。利用者向けの導入・運用手順は [README.md](README.md) を参照。

## 目次

- [アーキテクチャ](#アーキテクチャ)
- [ディレクトリ構成](#ディレクトリ構成)
- [開発環境の準備](#開発環境の準備)
- [ローカルでの起動](#ローカルでの起動)
- [環境変数](#環境変数)
- [API 仕様](#api-仕様)
- [Redis のデータ構造](#redis-のデータ構造)
- [実装上のポイント](#実装上のポイント)
- [Alpine インストーラ](#alpine-インストーラ)
- [CI](#ci)
- [テスト](#テスト)
- [Lint](#lint)
- [後片付け](#後片付け)

---

## アーキテクチャ

```text
クライアント ─POST /download─▶ API サーバ ─(yt-dlp -j で解析)─▶ Redis(ytdlp:queue)
                                                                     │ BLPOP
                                                                     ▼
                                                            Worker(yt-dlp 実行)─▶ 保存先
                                  ┌─ pot-provider (PO Token) ◀─ API / Worker から参照
```

| コンポーネント | 役割                                                                                     |
| -------------- | ---------------------------------------------------------------------------------------- |
| API サーバ     | リクエストを検証し、`yt-dlp -j --flat-playlist` で解析してジョブに分解し、キューへ積む。 |
| Redis          | ジョブキューとジョブ状態の保管。                                                         |
| Worker         | キューから 1 件取得して yt-dlp を実行する。並列化はコンテナ（プロセス）の数で行う。      |
| pot-provider   | YouTube 用の PO Token を発行する（bgutil）。API / Worker の yt-dlp が参照する。          |
| Redis Insight  | Redis の閲覧用 Web UI（任意）。                                                          |

## ディレクトリ構成

| パス                                                                       | 内容                                                              |
| -------------------------------------------------------------------------- | ----------------------------------------------------------------- |
| [apiServer/](apiServer/)                                                   | API サーバ（Flask + waitress）。`src/main.py`（ルーティング）、`src/function.py`（yt-dlp 解析・ジョブ生成） |
| [workerServer/](workerServer/)                                             | Worker。`src/main.py`（キュー処理・状態遷移）、`src/function.py`（yt-dlp 実行） |
| `*/src/cookies.py`                                                         | cookie プロファイルの共通処理（API・Worker・browserServer で**同一内容**。一部だけ直さない） |
| [browserServer/](browserServer/)                                           | ログイン用ブラウザ（Chromium、画面は CDP で配信）と cookie 回収。`src/main.py`（操作画面・制御 API・WebSocket、aiohttp）、`src/session.py`（状態管理）、`src/browser.py`（ブラウザ制御・画面配信・入力）、`src/netscape.py`（cookie の絞り込み・変換） |
| [tests/](tests/)                                                           | 単体テスト（標準 `unittest`）                                     |
| [specs/](specs/)                                                           | 要件・設計・タスク（仕様駆動開発）。全体用と、アプリ別（`apiServer/` `workerServer/` `browserServer/`） |
| `*/yt-dlp.conf`                                                            | イメージ内の `/etc/yt-dlp.conf` になる yt-dlp 共通設定            |
| [nginx/](nginx/)                                                           | HTTPS 構成用の nginx イメージ                                     |
| [docker-compose.yml](docker-compose.yml)                                   | 基本構成（HTTP）。`.nginx.yml` / `.cloudflare.yml` は派生構成     |
| [scripts/install-alpine.sh](scripts/install-alpine.sh)                     | Docker 無しの Alpine 向けインストーラ                             |
| [.github/workflows/release-installer.yml](.github/workflows/release-installer.yml) | インストーラの配布用 CI                                   |
| [MEMO.md](MEMO.md)                                                         | yt-dlp のオプションに関するメモ                                   |

## 開発環境の準備

### Python

Python 3.12 を使う。[.python-version](.python-version) は pyenv の仮想環境 `ytdlpServer` を指す。

```sh
pyenv virtualenv 3.12.11 ytdlpServer
pip install -r apiServer/requirements.txt -r workerServer/requirements.txt
```

ローカル実行時は、`yt-dlp` が PATH から呼べること、Worker では加えて `ffmpeg` が必要なこと、
YouTube の JS チャレンジ(EJS)のために `node` が必要なことに注意する。

### Redis

```sh
docker run --name redis-ytdlp -p 6379:6379 -d --rm redis:8.4.0
```

### Redis Insight（DB 閲覧）

```sh
docker run --rm -d --name redisinsight -p 5540:5540 redis/redisinsight:latest
```

起動後、`http://localhost:5540` で Redis（`host.docker.internal` またはホストの IP、ポート 6379）を登録する。
キーは `ytdlp:queue`（ジョブキュー）と `ytdlp:jobs:<status>:<job_id>`（ジョブ状態）を見る。

Alpine 向けインストーラー（`scripts/install-alpine.sh`）は、コンテナを使わずに Redis Insight を動かす。
Redis Insight は SSPL のためビルド済みバイナリを再配布せず、公式 GitHub のタグのソースをインストール先でビルドする。
手順は公式 Dockerfile と同じ（3.8.0 は yarn、それ以降の版は npm に移行済みのため `yarn.lock` の有無で分岐する）。
詳細は [Alpine インストーラ](#alpine-インストーラ) を参照。

## ローカルでの起動

### 全体を compose で起動する（推奨）

pot-provider を含めて一括で起動できる。

```sh
docker compose up -d --build
docker compose logs -f api worker
```

ソースを変更したら `docker compose up -d --build api worker` で再ビルドする。

### コンテナを個別に起動する

> **注意**: `yt-dlp.conf` は PO Token プロバイダの接続先を `http://pot-provider:4416` に固定している。
> 個別起動では、この名前が解決できるよう pot-provider を同じネットワークに置く必要がある（無い場合、YouTube の取得に失敗することがある）。

```sh
docker network create ytdlp-dev
docker run -d --rm --name pot-provider --network ytdlp-dev brainicism/bgutil-ytdlp-pot-provider
docker run -d --rm --name redis-ytdlp --network ytdlp-dev -p 6379:6379 redis:8.4.0
```

#### API Server

```sh
# build
docker build ./apiServer -t ytdlpserver-api
# Run debug mode with redis
docker run --rm --name ytdlp-api --network ytdlp-dev -p 5000:5000 -e DEBUG=true -e REDIS_URL=redis://redis-ytdlp:6379 ytdlpserver-api:latest
```

```log
Requirement already satisfied: yt-dlp in /usr/lib/python3.12/site-packages (2025.12.8)
INFO: yt-dlp updated to latest version.
INFO: Connected to Redis at redis://192.168.3.151:6379
INFO: Start ytdlpServer port: 5000
```

- 起動時に yt-dlp を最新版へ更新する（[apiServer/entrypoint.sh](apiServer/entrypoint.sh)）。
- `SERVER_TTL`（時間、既定 24）が経過すると API プロセスを停止する。コンテナは `restart: always`
  （Alpine 版は supervise-daemon の respawn）で再起動され、そのたびに yt-dlp が更新される。
- yt-dlp の解析（probe）に失敗した場合も、API は 400 を返した後にプロセスを終了して再起動させる（yt-dlp の更新を促すため）。
- `DEBUG` に空でない値を設定すると、Redis に接続できなくても起動し、リクエストやジョブの内容をログ出力する。
  ただし Redis が無いとジョブは積めない。

#### Worker Server

```sh
# build
docker build ./workerServer -t ytdlpserver-worker

# Run debug mode with redis
docker run --rm --name ytdlp-worker --network ytdlp-dev -v /mnt/video:/download -e REDIS_URL=redis://redis-ytdlp:6379 ytdlpserver-worker:latest
```

- **Worker は 1 件処理すると終了する**（[workerServer/src/main.py](workerServer/src/main.py) の `main()`）。
  再実行可能な失敗ジョブがあればそれを優先し、なければキューを最大 `BRPOP_TIMEOUT` 秒待つ。
  常駐させるには `restart: always`（compose）や `--restart` などで再起動させる。上記の `docker run --rm` は 1 件で終わる。
- 並列処理は実装していない。Worker の数を増やして対応する（`docker compose up -d --scale worker=N`）。
- 起動時に Redis へ接続できないと即終了する。

## 環境変数

### API サーバ

| 変数        | 既定値                   | 内容                                              |
| ----------- | ------------------------ | ------------------------------------------------- |
| REDIS_URL   | `redis://localhost:6379` | Redis の接続先                                    |
| PORT        | `5000`                   | 待ち受けポート（`0.0.0.0`）                       |
| DEBUG       | 未設定                   | 空でない値でデバッグモード                        |
| SERVER_TTL  | `24`                     | 定期再起動の間隔（時間）。数値以外は 24 になる    |
| COOKIE_DIR  | `/cookies`               | cookie プロファイルの置き場所（Worker と共有する） |
| BROWSER_UI_URL | 未設定                | ログイン要求の応答に載せる `login_url` の基準 URL |

### browserServer

| 変数            | 既定値      | 内容                                                  |
| --------------- | ----------- | ----------------------------------------------------- |
| PORT            | `8080`      | 操作画面・制御 API のポート                           |
| SESSION_TIMEOUT | `900`       | ログイン操作の自動終了までの秒数                      |
| COOKIE_DIR      | `/cookies`  | cookie プロファイルの置き場所                         |

### Worker

| 変数          | 既定値                   | 内容                                                |
| ------------- | ------------------------ | --------------------------------------------------- |
| REDIS_URL     | `redis://localhost:6379` | Redis の接続先                                      |
| REDIS_TTL     | `604800`（7 日）         | ジョブ状態キーの有効期間（秒）                      |
| RETRY_COUNT   | `5`                      | 失敗ジョブの自動リトライ上限（`failed_count` がこの値未満なら対象） |
| BRPOP_TIMEOUT | `60`                     | キュー待ちのタイムアウト（秒）                      |
| DOWNLOAD_DIR  | `/download`              | 保存先のルート                                      |
| COOKIE_DIR    | `/cookies`               | cookie プロファイルの置き場所（API と共有する）     |
| BROWSER_UI_URL | 未設定                  | ジョブの `login_url` の基準 URL                     |

## API 仕様

実装は [apiServer/src/main.py](apiServer/src/main.py)。リクエストは JSON。

| メソッド | パス                 | 内容                                                                     |
| -------- | -------------------- | ------------------------------------------------------------------------ |
| POST     | `/download`          | URL を解析してジョブをキューへ積む                                       |
| POST     | `/schedule`          | リクエストを解析せず保存だけする（後でまとめて実行するため）             |
| GET      | `/schedule`          | 保存済みリクエストの一覧（`options` は含めない）                         |
| POST     | `/download/scheduled`| 保存済みリクエストを解析してキューへ積む。`{"count": N \| "all"}`（省略時は all） |
| POST     | `/download/retry`    | 失敗ジョブの `failed_count` を 0 に戻し、Worker が再試行できるようにする |
| GET      | `/auth/profiles`     | cookie プロファイルの名前・状態・更新時刻の一覧（cookie の中身は返さない） |

### リクエスト項目（`/download`・`/schedule`）

| 項目      | 型     | 必須 | 内容                                                                     |
| --------- | ------ | ---- | ------------------------------------------------------------------------ |
| url       | string | 必須 | ダウンロード対象の URL。空文字は不可                                     |
| options   | string | 任意 | yt-dlp のオプション。空白区切りで分割して配列化する（引用符は解釈しない）|
| savedir   | string | 任意 | 保存先のサブディレクトリ                                                 |
| namefield | string | 任意 | ファイル名テンプレート（`%(key)s` 形式）。空白のみは未指定扱い           |
| auth_profile | string | 任意 | cookie プロファイル名（`[A-Za-z0-9_-]{1,64}`）。空文字は未指定扱い |

- `options` が文字列以外だと 400（`Invalid request.`）になる。
- `options` に `--cookies` / `--cookies-from-browser` / `-u` `--username` / `-p` `--password` / `--twofactor` / `-n` `--netrc*` を含むと 400（該当オプション名を `message` に返す）。
- `savedir` は NFC 正規化し、`\ / ¥ : * ? " < > |` を `_` に置換、全角スペースを除去し、連続空白を 1 つにまとめる。

### レスポンス

| ケース                       | ステータス | message                                        |
| ---------------------------- | ---------- | ---------------------------------------------- |
| 受理                         | 200        | `Request accepted.`                            |
| パラメータ不正               | 400        | `Invalid request.`（禁止オプション・不正な `auth_profile` は理由を返す） |
| ログインが必要               | 401        | `error=login_required`、`reason`（`cookie_missing` / `profile_unknown` / `cookie_expired`）、`auth_profile`、日本語の `message`、`login_url` を返す。プロセスは再起動しない |
| namefield が不正             | 400        | namefield のエラー内容                         |
| yt-dlp の解析失敗            | 400        | `yt-dlp probe failed; wait restart yt-dlp.`（その後プロセスを終了して再起動） |
| 内部エラー                   | 500        | `Internal server error.`                       |

### リクエスト例

```sh
curl -H "Content-type: application/json" -X POST "http://192.168.3.152:5000/download" -d '{"url":"<video url>","options":"--format bv*[vcodec^=avc1][ext=mp4]+ba[ext=m4a][language^=ja]/bv*[vcodec^=avc1][ext=mp4]+ba[ext=m4a]/best --no-playlist --windows-filenames --merge-output-format mp4", "savedir": "temp", "namefield": "%(title)s [%(id)s]"}'
```

スケジュール実行の例:

```sh
# 保存
curl -H "Content-Type: application/json" -X POST http://localhost:5000/schedule -d '{"url":"<video url>"}'
# 一覧
curl http://localhost:5000/schedule
# 2 件だけ実行
curl -H "Content-Type: application/json" -X POST http://localhost:5000/download/scheduled -d '{"count": 2}'
# 失敗ジョブを再試行
curl -X POST http://localhost:5000/download/retry
```

## Redis のデータ構造

### ジョブキュー（`ytdlp:queue`）

List。API が `RPUSH`、Worker が `BLPOP` する。要素は次の JSON。

| 項目     | 内容                                                                    |
| -------- | ----------------------------------------------------------------------- |
| id       | ジョブ ID（namefield 指定時は生成した名前、それ以外は yt-dlp の動画 ID）|
| url      | 対象 URL（`webpage_url`、無ければ `url`、それも無ければ要求 URL）      |
| options  | オプションの配列（`--no-playlist` は解析時のみ除外し、ジョブには残す）|
| savedir  | サブディレクトリ                                                        |
| filename | 拡張子を除いたファイル名                                                |
| auth_profile | cookie プロファイル名（指定時のみ）                                 |

プレイリストは要素ごとに 1 ジョブへ分解される。

### 予約リクエスト（`ytdlp:requests`）

List。`/schedule` が保存した `url` / `options` / `savedir` / `namefield` / `auth_profile` の JSON。
`/download/scheduled` で 401（ログイン要求）になった要素は、先頭へ戻して残す。

### ジョブ状態（`ytdlp:jobs:<status>:<job_id>`）

Hash。`status` がキー名に入るため、状態が変わると **キーごと作り直す**（新キーへ書き込み、旧キーを削除）。
遷移は `pending` → `in_progress` → `completed` / `failed`。いずれも `REDIS_TTL` で期限切れになる。

| フィールド   | 内容                                            |
| ------------ | ----------------------------------------------- |
| status       | `pending` / `in_progress` / `completed` / `failed` |
| url / options / savedir / filename | ジョブの内容（options は JSON 文字列） |
| created_at   | 作成時刻（UNIX 秒）                             |
| started_at / completed_at / failed_at | 各時刻                      |
| output       | 成功時の yt-dlp の標準出力                      |
| error        | 失敗時のエラー内容                              |
| failed_count | 失敗回数。`RETRY_COUNT` に達すると自動リトライされない |
| auth_profile | cookie プロファイル名（未指定は空）             |
| error_code   | ログイン要求で失敗したとき `login_required`。それ以外の失敗では空 |
| login_url    | `BROWSER_UI_URL` があるときの再ログイン先       |

同じ ID のジョブを重複して積むと、状態キーが上書きされる点に注意する。

### cookie プロファイルの状態（`ytdlp:auth:profile:<name>`）

Hash。ログイン要求を検知したときだけ作られ、`status=expired` と `expired_at`（UNIX 秒）を持つ。
cookie ファイル本体は Redis に置かない（`COOKIE_DIR/<name>.txt`）。
`expired_at` 以降に更新された cookie ファイルがあれば有効に戻る（cookie を置き直せば自動復帰する）。

## 実装上のポイント

### ジョブ生成（[apiServer/src/function.py](apiServer/src/function.py)）

- 解析は `yt-dlp -j --no-progress --flat-playlist <options> <url>` を実行し、出力の各行（JSON）を 1 ジョブにする。
- `--no-playlist` は解析時に取り除く（プレイリスト全体を展開するため）。Worker の実行時には付与する。
- namefield は `%(key)s` を解析結果で置換する。キーが無い・空の場合や、置換後が空の場合は `ValueError`（400）。
  同名が複数できる場合は 2 件目以降に `-2`, `-3` … を付ける。
- ファイル名・ジョブ ID は、`\ / ¥ : * ? " < > |` を `_` にし、ジョブ ID は 200 バイトに切り詰める。

### YouTube 対応

- `with_youtube_defaults()` は API・Worker の両方に同じものがある。`--extractor-args youtube:...` に
  `player_client=default,mweb` を補う（子供向け動画などを取得するため）。ユーザが `player_client` を指定していればそれを優先する。
  **修正するときは両方を揃える。**
- PO Token は bgutil の HTTP プロバイダ（`pot-provider:4416`）から取得する。`yt-dlp.conf` で指定している。
- JS チャレンジ(EJS)には `--js-runtimes node` を使う（Alpine では deno が使えないため）。

### Worker（[workerServer/src/function.py](workerServer/src/function.py)）

- 保存先は `DOWNLOAD_DIR/<savedir>/<filename>.%(ext)s` で、yt-dlp が最終位置へ直接出力する（一時ディレクトリ経由のコピーはしない）。
- `savedir` / `filename` は Worker 側でも同じ規則で無害化する。
- 失敗すると `failed_count` を加算して `failed` に移す。起動のたびに `failed_count < RETRY_COUNT` の失敗ジョブを探し、
  あればキューより優先して再試行する。

### cookie によるログイン

設計は [specs/design.md](specs/design.md)（全体）、[specs/apiServer/design.md](specs/apiServer/design.md)、[specs/workerServer/design.md](specs/workerServer/design.md) を参照する。
共通処理は `*/src/cookies.py`（3 アプリで同一内容。**修正するときは全て揃える**。一致はテストで確認する）。

### コンテナイメージ

- どちらも `alpine:3.21` ベース。API のイメージには `nginx` も含まれるが、API のコードからは使っていない。
- Worker は `ffmpeg` / `mutagen` を含む。
- 起動時に `pip install --upgrade yt-dlp` を行うため、更新に失敗した場合は、イメージに含まれる版の yt-dlp のまま起動する（更新の成否はログに出ない）。

## Alpine インストーラ

[scripts/install-alpine.sh](scripts/install-alpine.sh) は Docker を使わず、Alpine Linux へ全構成を導入する冪等なスクリプト。

- 設定は環境変数で上書きでき、`/etc/conf.d/ytdlpserver` に保存される。再実行時は保存済みの値を引き継ぐ（優先順位: 環境変数 > 保存済み > 既定値）。
- 環境変数の一覧は `sh scripts/install-alpine.sh --help` で確認できる。
- ソースは `INSTALL_DIR`（既定 `/opt/ytdlpserver`）へ取得し、Python は venv（`--system-site-packages`）に入れる。
- Redis / pot-provider / API / Worker は OpenRC サービスとして登録する。pot-provider と Redis は `127.0.0.1` に限定する。
- 任意で nginx（HTTPS）、cloudflared、Redis の Web UI を導入する。

### Redis Insight のビルド

- `REDIS_UI=insight`（既定）は、`REDIS_INSIGHT_VERSION`（既定 3.8.0）のタグをインストール先でビルドする。
- Node.js 24 以上とメモリ 2GB 程度が必要。条件を満たさない・ビルドに失敗した場合は `redis-commander` にフォールバックする。
- `RI_BUILD_STORAGE` でビルド先を選ぶ。

| 値     | 内容                                                                                   |
| ------ | -------------------------------------------------------------------------------------- |
| tmpfs  | 作業領域をメモリ上に置く。メモリ 12GB 程度が必要（ピーク約 9.5GB）。終了後に解放される |
| disk   | ディスクを使う。空き 10GB 程度が必要。ビルド後に削除される                             |
| auto   | メモリが足りれば tmpfs、足りない・マウントできなければ disk                            |

- tmpfs のマウントには LXC の権限が必要。マウントできない場合は auto なら disk にフォールバックし、tmpfs 指定ならエラーにする。
- 前回の中断で tmpfs が残っていても再実行できる。

### 動作確認

`sh -n` と `shellcheck -s sh` を通すこと（CI でも実施）。

```sh
sh -n scripts/install-alpine.sh
shellcheck -s sh scripts/install-alpine.sh
```

## CI

[.github/workflows/release-installer.yml](.github/workflows/release-installer.yml) が、`scripts/install-alpine.sh` などの変更で動く。

1. `sh -n` と `shellcheck` で構文チェックする。
2. `REPO_REF_DEFAULT`（ブランチ / タグ名）と、cloudflared の最新版・SHA256 を、スクリプトの `*_DEFAULT=` 行へ埋め込む。
3. 公開する。
   - タグ（`v*`）: 通常の Release に `install-alpine.sh` を添付する。
   - ブランチ: ブランチ名（`/` は `-`）の prerelease に添付し、自動更新する。

スクリプトの `REPO_REF_DEFAULT=` などの行頭書式を変えると埋め込みが失敗するため、変更時は CI の `sed` / `grep` も合わせて確認する。

## テスト

標準の `unittest` で、Redis やネットワークは不要（Redis は疑似実装、yt-dlp は差し替え）。依存は API・browserServer の `requirements.txt` のとおり。

```sh
pip install -r apiServer/requirements.txt -r browserServer/requirements.txt
python3 -m unittest discover -s tests -v
```

- `tests/test_cookies.py`: 共通処理（判定・禁止オプション・書き戻し・状態）。API と Worker の `cookies.py` が同一であることも確認する。
- `tests/test_api.py` / `tests/test_worker.py`: 401 応答、予約の保持、リトライ除外、ログの伏せ字など。
- `tests/test_browser.py`: cookie の絞り込み・変換、入力の CDP への変換、セッションの状態遷移（排他・タイムアウト・失敗時の破棄）、制御 API と WebSocket。Chromium と CDP は差し替える。

## Lint

[apiServer/pyproject.toml](apiServer/pyproject.toml)・[workerServer/pyproject.toml](workerServer/pyproject.toml)・[browserServer/pyproject.toml](browserServer/pyproject.toml) に ruff の設定がある（`select = ["ALL"]` から一部を除外）。

```sh
ruff check apiServer/src workerServer/src browserServer/src
```

## 後片付け

```sh
docker rm -f redis-ytdlp redisinsight ytdlp-api ytdlp-worker pot-provider
docker network rm ytdlp-dev
docker container prune
```

compose で起動した場合は `docker compose down` を使う。
