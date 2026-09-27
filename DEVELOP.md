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
                                                                     │ PUBLISH ytdlp:events
                                                                     ▼
                                                            dispatcher(常駐・軽量)
                                                                     │ 対象があるときだけ起動
                                                                     ▼
                                                            Worker(yt-dlp 実行・1 件で終了)─▶ 保存先
                                  ┌─ pot-provider (PO Token) ◀─ API / Worker から参照
                                  └─ GitHub Releases ◀─ dispatcher が yt-dlp の新版を定期確認
```

| コンポーネント | 役割                                                                                     |
| -------------- | ---------------------------------------------------------------------------------------- |
| API サーバ     | リクエストを検証し、`yt-dlp -j --flat-playlist` で解析してジョブに分解し、キューへ積む。yt-dlp は更新しない（再起動もしない）。 |
| Redis          | ジョブキューとジョブ状態の保管。dispatcher・worker 間の通知（Pub/Sub）も兼ねる。          |
| dispatcher     | 常駐。ジョブがある間だけ worker を起動する（[workerServer/src/dispatcher.py](workerServer/src/dispatcher.py)）。yt-dlp の新版を定期的に確認・適用する（[workerServer/src/updater.py](workerServer/src/updater.py)）。 |
| Worker         | キューから 1 件取得して yt-dlp を実行し、終了する。並列化は dispatcher が起動する数（`WORKER_MAX`）で行う。 |
| pot-provider   | YouTube 用の PO Token を発行する（bgutil）。API / Worker の yt-dlp が参照する。          |
| docker-socket-proxy | （Compose のみ）dispatcher が worker コンテナを作るための、権限を絞った Docker API 代理。 |
| Redis Insight  | Redis の閲覧用 Web UI（任意）。                                                          |

イベント駆動の設計は [specs/design.md](specs/design.md)・[specs/workerServer/design.md](specs/workerServer/design.md) を参照。

## ディレクトリ構成

| パス                                                                       | 内容                                                              |
| -------------------------------------------------------------------------- | ----------------------------------------------------------------- |
| [apiServer/](apiServer/)                                                   | API サーバ（Flask + waitress）。`src/main.py`（ルーティング）、`src/function.py`（yt-dlp 解析・ジョブ生成） |
| [workerServer/](workerServer/)                                             | Worker と dispatcher（同一イメージ）。`src/main.py`（worker。取得・実行・状態遷移）、`src/dispatcher.py`（起動判断）、`src/updater.py`（yt-dlp の更新）、`src/jobs.py`（状態遷移・回収の共有処理）、`src/backends.py`（worker の起動先。プロセス/Docker）、`src/function.py`（yt-dlp 実行） |
| `*/src/cookies.py`・`*/src/presets.json`                                   | cookie プロファイルの共通処理と、プリセットの定義（API・Worker・browserServer で**同一内容**。一部だけ直さない） |
| [browserServer/](browserServer/)                                           | ログイン用ブラウザ（Chromium、画面は CDP で配信）と cookie 回収。`src/main.py`（操作画面・制御 API・WebSocket、aiohttp）、`src/session.py`（状態管理）、`src/browser.py`（ブラウザ制御・画面配信・入力）、`src/netscape.py`（cookie の絞り込み・変換） |
| [tests/](tests/)                                                           | 単体テスト（標準 `unittest`）                                     |
| [specs/](specs/)                                                           | 要件・設計・タスク（仕様駆動開発）。全体用と、アプリ別（`apiServer/` `workerServer/` `browserServer/`） |
| `*/yt-dlp.conf`                                                            | イメージ内の `/etc/yt-dlp.conf` になる yt-dlp 共通設定            |
| [nginx/](nginx/)                                                           | HTTPS 構成用の nginx イメージ                                     |
| [docker-compose.yml](docker-compose.yml)                                   | 基本構成（HTTP）。`.nginx.yml` / `.cloudflare.yml` は派生構成     |
| [install/install.sh.tmpl](install/install.sh.tmpl)                         | 頒布される `install.sh` のひな形（git 等の導入・ソース取得のみを行う薄い層） |
| [install/build-install.sh](install/build-install.sh)                       | ひな形へ REF・COMMIT・REPO_URL を埋め込み `install.sh` を生成する |
| [install/setup.sh](install/setup.sh)                                       | 本体インストーラ（Docker 無しの Alpine 向け）                     |
| [.github/workflows/installer.yml](.github/workflows/installer.yml)         | インストーラの配布用 CI                                           |
| [MEMO.md](MEMO.md)                                                         | yt-dlp のオプションに関するメモ                                   |

## 開発環境の準備

### Python

Python 3.12 を使う。[.python-version](.python-version) は pyenv の仮想環境 `ytdlpServer` を指す。

```sh
pyenv virtualenv 3.12.11 ytdlpServer
pip install -r apiServer/requirements.txt -r workerServer/requirements.txt
```

`requirements.txt` に yt-dlp 自体は含まない（本番はバイナリを別途導入する。[コンテナイメージ](#コンテナイメージ)参照）。
ローカル実行時は、`pip install yt-dlp` 等で別途 `yt-dlp` を PATH から呼べるようにすること、
Worker では加えて `ffmpeg` が必要なこと、YouTube の JS チャレンジ(EJS)のために `node` が必要なことに注意する。

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

Alpine 向けインストーラー（`install/setup.sh`）は、コンテナを使わずに Redis Insight を動かす。
Redis Insight は SSPL のためビルド済みバイナリを再配布せず、公式 GitHub のタグのソースをインストール先でビルドする。
手順は公式 Dockerfile と同じ（3.8.0 は yarn、それ以降の版は npm に移行済みのため `yarn.lock` の有無で分岐する）。
詳細は [Alpine インストーラ](#alpine-インストーラ) を参照。

## ローカルでの起動

### 全体を compose で起動する（推奨）

pot-provider を含めて一括で起動できる。

```sh
docker compose up -d --build
docker compose logs -f api dispatcher
```

ソースを変更したら `docker compose up -d --build api dispatcher` で再ビルドする。
worker は dispatcher がジョブに応じて起動・終了するため、compose の個別サービスとしては存在しない
（`docker compose ps` には、動いている間だけ `ytdlp.role=worker` ラベル付きのコンテナとして現れる）。

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
INFO: Connected to Redis at redis://192.168.3.151:6379
INFO: Start ytdlpServer port: 5000
```

- yt-dlp は更新しない（更新は dispatcher が一括で行う）。定期再起動（旧 `SERVER_TTL`）も行わない。
- yt-dlp の解析（probe）に失敗した場合は、400 を返し、dispatcher へ新版の確認を依頼する（`check_update` 通知。プロセスは再起動しない）。
- `DEBUG` に空でない値を設定すると、Redis に接続できなくても起動し、リクエストやジョブの内容をログ出力する。
  ただし Redis が無いとジョブは積めない。

#### Worker / dispatcher（workerServer、同一イメージ）

```sh
# build
docker build -f workerServer/Dockerfile -t ytdlpserver-worker .   # ビルドコンテキストはリポジトリ直下

# dispatcher を起動 (既定のコマンド)
docker run -d --name ytdlp-dispatcher --network ytdlp-dev \
  -v /mnt/video:/download -e REDIS_URL=redis://redis-ytdlp:6379 \
  -e DISPATCH_MODE=process ytdlpserver-worker:latest

# worker 単体を試す (dispatcher を経由せず、1 件処理して終了する)
docker run --rm --name ytdlp-worker --network ytdlp-dev \
  -v /mnt/video:/download -e REDIS_URL=redis://redis-ytdlp:6379 \
  ytdlpserver-worker:latest python3 -u /workspace/main.py
```

- **dispatcher は常駐し、ジョブ（キュー・自動リトライ対象）があるときだけ worker を起動する。**
  worker 自体は 1 件処理すると終了する（[workerServer/src/main.py](workerServer/src/main.py)）。
- `DISPATCH_MODE=docker`（既定は Compose の設定）では、docker-socket-proxy 経由で worker コンテナを作る。
  ローカルの単発検証では `DISPATCH_MODE=process`（dispatcher の子プロセスとして worker を起動）が簡単。
- 並列度は `WORKER_MAX`（既定 1）。dispatcher 自身が同時に動く worker の数を管理する。
- yt-dlp は dispatcher だけが更新する。導入先は `YTDLP_DIR`（既定 `/opt/ytdlp`）。
- 起動時に Redis へ接続できないと即終了する（dispatcher・worker とも）。

## 環境変数

### API サーバ

| 変数        | 既定値                   | 内容                                              |
| ----------- | ------------------------ | ------------------------------------------------- |
| REDIS_URL   | `redis://localhost:6379` | Redis の接続先                                    |
| PORT        | `5000`                   | 待ち受けポート（`0.0.0.0`）                       |
| DEBUG       | 未設定                   | 空でない値でデバッグモード                        |
| YTDLP_DIR   | `/opt/ytdlp`             | yt-dlp の導入先（dispatcher が更新する。`current` を `PATH` の先頭に置いて参照する） |
| COOKIE_DIR  | `/cookies`               | cookie プロファイルの置き場所（Worker と共有する） |
| BROWSER_UI_URL | 未設定                | ログイン要求の応答に載せる `login_url` の基準 URL |

### browserServer

| 変数            | 既定値      | 内容                                                  |
| --------------- | ----------- | ----------------------------------------------------- |
| PORT            | `8080`      | 操作画面・制御 API のポート                           |
| SESSION_TIMEOUT | `900`       | ログイン操作の自動終了までの秒数                      |
| COOKIE_DIR      | `/cookies`  | cookie プロファイルの置き場所                         |
| TZ              | 未設定（compose では `Asia/Tokyo`） | ブラウザのタイムゾーン。サーバの外向き IP の所在地に合わせる（UTC のままだと X のログインが制限される） |
| BROWSER_LANG    | `ja,en-US,en` | ブラウザの言語（`Accept-Language`・`navigator.languages`） |

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
| GET      | `/auth/profiles`     | cookie を保存済みのプロファイルの名前・表示名・対象ドメイン・状態・更新時刻の一覧（cookie の中身は返さない） |

### リクエスト項目（`/download`・`/schedule`）

| 項目      | 型     | 必須 | 内容                                                                     |
| --------- | ------ | ---- | ------------------------------------------------------------------------ |
| url       | string | 必須 | ダウンロード対象の URL。空文字は不可                                     |
| options   | string | 任意 | yt-dlp のオプション。空白区切りで分割して配列化する（引用符は解釈しない）|
| savedir   | string | 任意 | 保存先のサブディレクトリ                                                 |
| namefield | string | 任意 | ファイル名テンプレート（`%(key)s` 形式）。空白のみは未指定扱い           |
| auth_profile | string | 任意 | cookie プロファイル名（`[A-Za-z0-9_-]{1,64}`）。空文字は未指定扱い。省略時は URL のサイトから自動で選ぶ |

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
| yt-dlp の解析失敗            | 400        | `yt-dlp probe failed; requested a yt-dlp update check.`（プロセスは再起動しない。dispatcher へ新版の確認を依頼する） |
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

List。API が `RPUSH` する。要素は次の JSON。worker は `LMOVE` で 1 件を取り出す
（`ytdlp:processing:<worker_id>` へ一時的に移してから、`ytdlp:jobs:in_progress:<id>` の記録を作る。
[ライフサイクル](specs/workerServer/design.md) を参照）。

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

Hash。`status` がキー名に入るため、状態が変わると **キーごと作り直す**（`RENAME`、または新キーへ書き込み・旧キーを削除）。
遷移は `in_progress` → `completed` / `failed`（取得と同時に `in_progress` の記録を作るため、`pending` は経由しない）。
いずれも `REDIS_TTL` で期限切れになる。

| フィールド   | 内容                                            |
| ------------ | ----------------------------------------------- |
| status       | `in_progress` / `completed` / `failed`          |
| url / options / savedir / filename | ジョブの内容（options は JSON 文字列） |
| created_at   | 作成時刻（UNIX 秒）                             |
| started_at / completed_at / failed_at | 各時刻                      |
| worker_id    | 実行中の worker（ホスト名・PID・起動時刻から生成）。生存確認は `ytdlp:workers:<worker_id>`（TTL 付き文字列）で行う |
| output       | 成功時の yt-dlp の標準出力                      |
| error        | 失敗時のエラー内容                              |
| failed_count | 失敗回数。`RETRY_COUNT` に達すると自動リトライされない |
| auth_profile | cookie プロファイル名（未指定は空）             |
| error_code   | ログイン要求で失敗したとき `login_required`。停止指示・異常終了による中断は `interrupted`。それ以外の失敗では空 |
| login_url    | `BROWSER_UI_URL` があるときの再ログイン先       |

`error_code=interrupted` は、`failed_count` を消費しない場合（停止指示）と、消費する場合（dispatcher による異常終了の回収）がある。
同じ ID のジョブを重複して積むと、状態キーが上書きされる点に注意する（実行中に別の投入が来ると、記録の所有者が入れ替わりうる）。

### 通知チャンネル（`ytdlp:events`）

Pub/Sub。API が `queued`（ジョブ投入・`/download/retry`）、`check_update`（probe 失敗）を発行する。
dispatcher が購読し、内容は使わず「再評価せよ」の合図として扱う。取りこぼしは、dispatcher の定期スキャン（`DISPATCH_SCAN_INTERVAL`）が拾う。

### yt-dlp の更新状態（`ytdlp:updater`）

Hash。`checked_at`（前回の確認）、`current`（導入中の版）、`updated_at`、`last_error` を持つ。dispatcher が更新する。

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
  あればキューより優先して再試行する（取得は `RENAMENX` で行い、複数 worker が同じジョブを取り合わない）。
- 停止指示（SIGTERM/SIGINT）を受けたら yt-dlp を止め、`failed_count` を消費せず `interrupted` として記録する
  （dispatcher の停止時、compose の worker コンテナの停止時も同様）。

### cookie によるログイン

設計は [specs/design.md](specs/design.md)（全体）、[specs/apiServer/design.md](specs/apiServer/design.md)、[specs/workerServer/design.md](specs/workerServer/design.md) を参照する。
共通処理は `*/src/cookies.py`、プリセットの定義は `*/src/presets.json`（どちらも 3 アプリで同一内容。**修正するときは全て揃える**。一致はテストで確認する）。ユーザが追加したサイトは `COOKIE_DIR/profiles.json` に保存される。

### コンテナイメージ

- どちらも `alpine:3.21` ベース。API のイメージには `nginx` も含まれるが、API のコードからは使っていない。
- Worker は `ffmpeg` / `mutagen` を含む。
- yt-dlp は pip ではなく GitHub Releases の musllinux バイナリを使う（採用理由は [specs/design.md](specs/design.md) の「yt-dlp の配布方式」）。
  ビルド時に初期版を `/opt/ytdlp-image` へ同梱し、実行時は名前付きボリューム `ytdlp-bin`（`/opt/ytdlp`）を `PATH` の先頭で参照する
  （`/opt/ytdlp-image` はボリュームが空・壊れているときのフォールバック）。更新は dispatcher が一括で行い、
  api・worker は再起動なしに新版を使う。
- bgutil の yt-dlp プラグインは `/etc/yt-dlp/plugins/bgutil`（標準の置き場）に配置する。バイナリ版 yt-dlp は
  pip 環境の `yt_dlp_plugins` を自動検出しないため。

## Alpine インストーラ

[install/](install/) は、Docker を使わず Alpine Linux へ全構成を導入する冪等なインストーラ一式。2層構成で、役割分担は変えない（[.claude/bootstrap-ci-builder/SKILL.md](.claude/bootstrap-ci-builder/SKILL.md) 参照）。

| ファイル | 役割 |
| --- | --- |
| `install/install.sh.tmpl` | 頒布される `install.sh` のひな形。git・ca-certificates の導入と、埋め込まれたコミット（`@@COMMIT@@`）のソース取得だけを行う薄い層。取得後、同じコミットの `install/setup.sh` へ引数をそのまま渡して実行する |
| `install/build-install.sh` | `install.sh.tmpl` へ REF・COMMIT・REPO_URL を埋め込み、標準出力へ `install.sh` を出す生成スクリプト |
| `install/setup.sh` | 実際の導入・アンインストール処理を行う本体インストーラ |

利用者は Release から `install.sh` を取得して実行するだけでよく、`install/setup.sh` を直接意識する必要はない（[README.md](README.md) 参照）。

- 設定は環境変数で上書きでき、`/etc/conf.d/ytdlpserver` に保存される。再実行時は保存済みの値を引き継ぐ（優先順位: 環境変数 > 保存済み > 既定値）。
- 環境変数の一覧は `sh install/setup.sh --help` で確認できる。
- `--uninstall` でアプリ本体だけを削除できる（設定・cookie・Redis のデータ・動画の保存先は残す）。旧バージョンの安全なアンインストール + 新バージョンの導入に使う。
- ソースは `INSTALL_DIR`（既定 `/opt/ytdlpserver`）そのもの。`install.sh` がここへ git clone し、直下の `install/setup.sh` を実行する。
  venv・bin・pot-provider・redisinsight・ytdlp・cookies は、その兄弟ディレクトリとして展開する（いずれも `.gitignore` 済み）。Python は venv（`--system-site-packages`）に入れる。
- yt-dlp は `YTDLP_DIR`（既定 `$INSTALL_DIR/ytdlp`）へ GitHub Releases から導入し、`current` を Redis と同様に更新する。
  bgutil プラグインは `/etc/yt-dlp/plugins/bgutil` に配置する。
- Redis / pot-provider / API / dispatcher は OpenRC サービスとして登録する。pot-provider と Redis は `127.0.0.1` に限定する。
  worker は dispatcher の子プロセスとして起動される（OpenRC サービスとしては登録しない）。
- 任意で nginx（HTTPS）、cloudflared、Redis の Web UI を導入する。
- ログイン用ブラウザ（Chromium、`ytdlp-browser`）は `WITH_BROWSER=1` で既定で導入する。Alpine には musl 向けの `chromium` apk がそのまま使えるため、Redis Insight と違いその場でのビルドは不要。
  導入時にメモリ・ディスクの空きを確認し、閾値（メモリ 2048MB / ディスク空き 1024MB）未満なら、`WITH_BROWSER=1` が明示されていない限り自動で無効化する（Redis Insight のビルド失敗時に `redis-commander` へフォールバックするのと同じ考え方）。

### cloudflared の版数・整合性検証

`WITH_CLOUDFLARED=1` を指定すると、`install/setup.sh` の `setup_cloudflared()` が**実行時**に GitHub Releases から cloudflared の最新版を取得し、GitHub API がアセットごとに返すダイジェスト（`digest: sha256:...`）と突き合わせて検証する。
CI（`installer.yml`）側での版数・SHA256 の事前埋め込みは行わない（`install/setup.sh` は git checkout されたソースそのままで、`build-install.sh` のような CI による値の埋め込み対象にはできないため）。ダイジェストを取得できない場合は警告のうえ検証をスキップする。

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

`sh -n` と `shellcheck -S warning` を通すこと（CI でも実施）。

```sh
for f in install/*.sh; do sh -n "$f"; done
shellcheck -S warning install/*.sh
```

`install.sh` の生成（`install.sh.tmpl` への埋め込み）は手元でも試せる。

```sh
sh install/build-install.sh <REF> <40桁のコミットハッシュ> <REPO_URL> > /tmp/install.sh
sh -n /tmp/install.sh
```

### 設定スキーマ版（アップデート対応）

`REDIS_INSIGHT_VERSION` のように「リリースに追従させたい既定値」から解決した値は、一度 `/etc/conf.d/ytdlpserver` に保存されると、以降は保存済みの値が再読込されるだけになり、新しいインストーラの既定値に切り替わらない（放っておくと、新しいインストーラを実行してもアップデートされない）。

これを解決するため、スクリプト先頭の `SCRIPT_SCHEMA_VERSION` を、保存済みの `_SCHEMA_VERSION`（`CONF_FILE` 内、ユーザー設定ではない内部項目）と比較し、上がっていれば「アップデート」とみなして、明示的に環境変数で指定されていない限りその追従項目（`REDIS_INSIGHT_VERSION`）を保存済みの値ごと破棄し、新しい既定値を使わせる。

**次のいずれかを行った場合は `SCRIPT_SCHEMA_VERSION` を 1 上げること。**

- 追従させたい既定値の意味を変えた（例: `REDIS_INSIGHT_VERSION` の既定値を上げた）
- 追従対象の環境変数を増減した（上記の for ループの対象を変えた）
- `_ENV_KEYS` から項目を削除した（削除自体は毎回の `write_conf` の書き直しで自動的に行われるが、上げておくとアップデート扱いのログが出て利用者に伝わる）

`_ENV_KEYS` に無い項目が保存済みファイルに残っていた場合は、版数によらず毎回検出して警告し、次の `write_conf` で自動的に削除される。

## CI

[.github/workflows/installer.yml](.github/workflows/installer.yml) が、ブランチ・タグへの push ごとに動く（`install/setup.sh` は git checkout されたソースそのままなので、REPO_REF のように特定ファイルの変更だけをトリガーにはしない）。

1. `sh -n` と `shellcheck -S warning` で `install/*.sh` を構文チェックする。
2. `install/build-install.sh` に、そのコミットの REF（ブランチ / タグ名）・COMMIT（フルSHA）・REPO_URL を渡して `install.sh` を生成する（`install.sh.tmpl` の `@@REF@@` / `@@COMMIT@@` / `@@REPO_URL@@` を置換）。
3. 公開する。
   - タグ（`v*`）: 通常の Release に `install.sh`・`install.sh.sha256` を添付する。
   - ブランチ: ワークフローの artifact（`installer-<ブランチ名（/ は - に置換）>`）として保存する（Release は作らない）。

`install.sh.tmpl` の `@@REF@@` / `@@COMMIT@@` / `@@REPO_URL@@` はひな形内では置換せず残す約束（`build-install.sh` が置換する対象のため）。この形式を変えると `build-install.sh` の置換・置換漏れ検査が壊れるため、変更時は両者を合わせて確認する。

## テスト

標準の `unittest` で、Redis やネットワークは不要（Redis は疑似実装、yt-dlp は差し替え）。依存は API・browserServer の `requirements.txt` のとおり。

```sh
pip install -r apiServer/requirements.txt -r browserServer/requirements.txt
python3 -m unittest discover -s tests -v
```

- `tests/test_cookies.py`: 共通処理（判定・禁止オプション・書き戻し・状態・プロファイルの定義と解決・履歴）。3 アプリの `cookies.py` と `presets.json` が同一であることも確認する。
- `tests/test_api.py`: 401 応答、予約の保持、リトライ除外、ログの伏せ字、通知（`queued`/`check_update`）の発行など。
- `tests/test_worker.py`: yt-dlp 実行（Popen）、ジョブの取得・状態遷移・回収（`jobs.py`）、`run_once` の一連の流れ（完了・失敗・中断・所有権の喪失）。
- `tests/test_dispatcher.py`: yt-dlp の更新判定（`updater.py`）、起動先（`backends.py`）、起動数の計算・バックオフ（`dispatcher.py`）。
- `tests/test_browser.py`: cookie の絞り込み・変換、入力の CDP への変換、セッションの状態遷移（排他・タイムアウト・失敗時の破棄）、制御 API と WebSocket。Chromium と CDP は差し替える。

## Lint

[apiServer/pyproject.toml](apiServer/pyproject.toml)・[workerServer/pyproject.toml](workerServer/pyproject.toml)・[browserServer/pyproject.toml](browserServer/pyproject.toml) に ruff の設定がある（`select = ["ALL"]` から一部を除外）。

```sh
ruff check apiServer/src workerServer/src browserServer/src
```

## 後片付け

```sh
docker rm -f redis-ytdlp redisinsight ytdlp-api ytdlp-dispatcher ytdlp-worker pot-provider
docker network rm ytdlp-dev
docker container prune
```

compose で起動した場合は `docker compose down` を使う。
