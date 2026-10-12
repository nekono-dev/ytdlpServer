# API サーバ 設計書

## 1. 構成

| パス                                   | 責務                                                        |
| -------------------------------------- | ----------------------------------------------------------- |
| `apiServer/src/main.py`                | HTTP エンドポイント、要求の検証、401 の組み立て、通知の発行 |
| `apiServer/src/function.py`            | yt-dlp による解析とジョブの組み立て                         |
| `apiServer/Dockerfile`                 | API の実行イメージ                                          |
| `apiServer/entrypoint.sh`              | API の起動（更新処理や定期再起動を持たない）                |
| `tests/test_api.py`                    | API の単体テスト                                            |
| `tests/spec-trace/api-server.test.mjs` | 受け入れ条件のタグ付きで単体テストを実行する                |

## 2. 設計項目

### D-1: エンドポイントとジョブの積み込み (FR-1, FR-2, FR-3)

| メソッドとパス             | 内容                                                                        |
| -------------------------- | --------------------------------------------------------------------------- |
| `POST /download`           | url、options、savedir、namefield、auth_profile を受け、解析してキューへ積む |
| `POST /schedule`           | 同じ項目を予約キュー `ytdlp:requests` へ積む                                |
| `GET /schedule`            | 予約の一覧                                                                  |
| `POST /download/scheduled` | 予約を count 件（既定は全件）取り出し、`POST /download` と同じ処理を行う    |
| `POST /download/retry`     | 失敗ジョブの再実行を指示する                                                |
| `GET /auth/profiles`       | プロファイル一覧                                                            |

```python
def handle_download(url, options, savedir, namefield, auth_profile):
    profile, candidate = select_profile(url, auth_profile)
    jobs = probe_jobs(url, options, savedir, namefield, profile)   # yt-dlp -J を実行。各ジョブに一意な ID が付く
    push_jobs(jobs)        # RPUSH ytdlp:queue、続けて notify("queued")
    return {"message": "Request accepted."}, 200
```

ジョブと予約の JSON は auth_profile を含む。予約は実行時に cookie ストアの状態を見直す。

| 項目       | 規則                                                                                                 |
| ---------- | ---------------------------------------------------------------------------------------------------- |
| ジョブ ID  | 表示名（動画 ID またはファイル名）を整形して 191 バイト以内に切り詰め、`-` と UUID の先頭8桁を付ける |
| ファイル名 | 表示名のまま。同名の衝突はワーカーが保存時に避ける                                                   |

### D-2: 要求の検証 (FR-4, NFR-2)

| 入力         | 規則                                               | 違反時 |
| ------------ | -------------------------------------------------- | ------ |
| url          | 空でない文字列                                     | 400    |
| options      | 文字列。空白で分割。認証関連オプションを含まない   | 400    |
| auth_profile | 省略または空文字は未指定。指定時は名前の規則に合う | 400    |
| savedir      | 文字列。区切り文字と記号は `_` に置換する          | 変換   |

拒否の応答にはオプション名だけを含め、値を含めない。

### D-3: 401 の応答と判定 (FR-5, FR-6)

```json
{
  "error": "login_required",
  "reason": "cookie_expired",
  "auth_profile": "nico",
  "message": "…",
  "login_url": "http://host:8080/?profile=nico&start_url=https%3A%2F%2Fexample.com%2F"
}
```

| reason            | 条件                                                 | 解析の実行     |
| ----------------- | ---------------------------------------------------- | -------------- |
| `cookie_missing`  | プロファイル未指定で、解析がログイン要求を返した     | する           |
| `profile_unknown` | 指定プロファイルのファイルが無い                     | しない         |
| `cookie_expired`  | プロファイルが失効、または解析がログイン要求を返した | 失効中はしない |

解析がログイン要求を返し、かつプロファイルを使っていたときは、そのプロファイルを失効として記録する。ログイン要求ではプロセスを終了しない。

### D-4: プロファイルの自動選択 (FR-7)

```python
def select_profile(url, auth_profile):
    if auth_profile:
        return auth_profile, None            # 明示指定を優先
    candidate = cookies.resolve_profile(url)
    if candidate and cookies.profile_state(redis, candidate) == "valid":
        return candidate, None               # 有効な cookie があれば使う
    return None, candidate                   # cookie なしで解析し、候補を覚える
```

候補があるときのログイン要求は、候補のプロファイルと状態から reason を決める（未保存は `profile_unknown`、失効は `cookie_expired`）。自動で選んだプロファイルはジョブの auth_profile に入る。

### D-5: 予約実行 (FR-8, FR-10)

```python
entry = redis.lpop("ytdlp:requests")
try:
    msg, code = handle_download(**json.loads(entry))
except Exception:
    msg, code = None, 500
if code == 401:
    redis.lpush("ytdlp:requests", entry)         # 先頭へ戻して 401 を返す
    return msg, code
if code != 200:
    redis.rpush("ytdlp:requests:failed", entry)  # 失敗用の予約リストへ退避して続行する
    failed += 1
```

応答は `{"message": "Processed scheduled requests.", "count": "<処理した件数>", "failed": "<退避した件数>"}`。解析できない予約も退避する。

### D-6: 通知と新版確認の依頼 (FR-9, NFR-1)

| 契機                      | メッセージ     | 発行位置           |
| ------------------------- | -------------- | ------------------ |
| ジョブをキューへ積んだ    | `queued`       | `RPUSH` の後       |
| 再実行要求で件数が 1 以上 | `queued`       | 失敗回数を戻した後 |
| yt-dlp の解析が失敗した   | `check_update` | 400 を返す前       |

発行先は Redis のチャンネル `ytdlp:events`。発行に失敗したら警告を出すだけで、応答は変えない。400 の `message` は `yt-dlp probe failed; requested a yt-dlp update check.` とする。プロセスを終了する処理と、定期再起動の設定は持たない。

### D-7: 再実行要求 (FR-11)

```python
for key in redis.scan_iter(match="ytdlp:jobs:failed:*"):
    created = redis.hset(key, "failed_count", "0")   # 項目単位で更新する
    if created:                                       # 直前に取得されて存在しなかったキー
        redis.delete(key)
        continue
    reset_count += 1
if reset_count:
    notify("queued")
```

### D-8: プロファイル一覧 (FR-12)

`GET /auth/profiles` は cookie ストアの一覧をそのまま返す。cookie の中身を含まない。

```json
[
  {
    "profile": "nico",
    "label": "ニコニコ",
    "domains": ["nicovideo.jp"],
    "status": "valid",
    "updated_at": 1790000000.0,
    "login_url": "http://host:8080/?profile=nico"
  }
]
```

## 3. エラー処理

| 事象                 | 処理                                |
| -------------------- | ----------------------------------- |
| 要求の形式の誤り     | 400 とメッセージ                    |
| ログイン要求         | 401（D-3 の形式）。予約は先頭へ戻す |
| yt-dlp の解析失敗    | 400 と新版確認の通知                |
| Redis に接続できない | 500                                 |
| 通知の発行失敗       | 警告を出し、応答は変えない          |
| 予期しない例外       | 500                                 |
