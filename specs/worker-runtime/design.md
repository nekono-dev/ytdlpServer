# ワーカー 設計書

## 1. 構成

| パス                                       | 責務                                              |
| ------------------------------------------ | ------------------------------------------------- |
| `workerServer/src/main.py`                 | 1件の取得、実行、記録、停止の処理、リースの更新   |
| `workerServer/src/jobs.py`                 | ジョブの取得、状態の遷移、リース、所有者の確認    |
| `workerServer/src/function.py`             | yt-dlp の実行、出力先の組み立て、YouTube の既定値 |
| `workerServer/yt-dlp.conf`                 | yt-dlp の共通設定                                 |
| `tests/test_worker.py`                     | ワーカーの単体テスト                              |
| `tests/test_worker_runtime.py`             | 停止、リースの失効、出力先の単体テスト            |
| `tests/spec-trace/worker-runtime.test.mjs` | 受け入れ条件のタグ付きで単体テストを実行する      |

## 2. 設計項目

### D-1: 取得 (FR-1, FR-2, FR-3)

```text
run_once(worker_id, stop)
  1. リトライ対象を探し、RENAMENX ytdlp:jobs:failed:<id> → ytdlp:jobs:in_progress:<id> で取得する
     成功したワーカーだけが所有者になる。失敗なら次の候補へ
  2. 無ければ LMOVE ytdlp:queue → ytdlp:processing:<worker_id>（LEFT → RIGHT）で1件を移す
     空なら終了（待たない）
  3. JSON を解析する。解析できない、または id が無い → ytdlp:queue:dead へ退避して終了コード 0
  4. ytdlp:jobs:in_progress:<id> を worker_id と started_at つきで作り、続けて処理中の記録を削除する
```

| キー                           | 型   | 内容                                                          |
| ------------------------------ | ---- | ------------------------------------------------------------- |
| `ytdlp:queue`                  | list | API が積むジョブ                                              |
| `ytdlp:processing:<worker_id>` | list | 取り出したが処理中の記録をまだ作っていないジョブ（最大1要素） |
| `ytdlp:queue:dead`             | list | 解析できないジョブ                                            |
| `ytdlp:jobs:<status>:<id>`     | hash | ジョブの記録。状態はキー名に持つ                              |

リトライ対象は、失敗回数がリトライ回数未満で、ログイン待ちでないもの。

### D-2: リースと所有者 (FR-4, FR-5, FR-7, NFR-2)

```python
SET ytdlp:workers:<worker_id> 1 EX LEASE_TTL      # 起動時
# ハートビートのスレッドが HEARTBEAT_INTERVAL ごとに更新する
def _heartbeat_loop(worker_id, finished, stop_requested):
    last_success = time.time()
    while not finished.is_set():
        try:
            renew_lease(client, worker_id, LEASE_TTL); last_success = time.time()
        except Exception:
            if time.time() - last_success >= LEASE_TTL:
                stop_requested.set()          # 停止の指示と同じ経路で中止する
        finished.wait(HEARTBEAT_INTERVAL)
```

完了・失敗・中断を記録する前に、`ytdlp:jobs:in_progress:<id>` の `worker_id` が自分であることを確認する。異なるときは書き込まずに終了する。終了時にリースを削除する。

### D-3: 停止の指示 (FR-6, NFR-1)

```text
SIGTERM / SIGINT → 停止のフラグを立てる
  実行中: yt-dlp に SIGTERM を送り、STOP_GRACE 秒（既定 20）待つ。終わらなければ SIGKILL
  ジョブを failed へ移し、error_code=interrupted、error に中断の理由を入れ、failed_count は変えない
  リースを削除して終了コード 0 で終了する
```

取得の直後に停止の指示が来ていた場合も、実行せずに同じ記録に戻す。

### D-4: 実行と出力先 (FR-8, FR-9)

```python
base = safe_name or str(job_id)                       # ファイル名が空ならジョブの識別子
outtmpl = str(SAVEDIR / subpath / (base + ".%(ext)s"))   # 拡張子は yt-dlp が決める
cmd = ["yt-dlp", "--no-progress", *cookie_opts, *with_youtube_defaults(options),
       "-o", outtmpl, "--no-playlist", url]
```

| 項目             | 規則                                                                                                                                    |
| ---------------- | --------------------------------------------------------------------------------------------------------------------------------------- |
| 保存先           | `DOWNLOAD_DIR`（既定 `/download`）配下の `savedir`。NFC に正規化し、無ければ作る                                                        |
| ファイル名       | NFC に正規化し、区切り文字と記号を `_` に置換し、空白を詰める                                                                           |
| 再実行           | 同じ保存先・同じ名前を使う。`--no-continue` を足さず、途中のファイルから再開できる                                                      |
| YouTube の既定値 | `--extractor-args youtube:` に `player_client=default,mweb` を補う。`player_client=` が既にあれば変えない。指定が無ければ引数ごと加える |

### D-5: cookie を使った実行 (FR-10, NFR-3)

| 場面                         | 動作                                                                   |
| ---------------------------- | ---------------------------------------------------------------------- |
| プロファイルつきの実行       | cookie ストアの一時コピーを `--cookies` に渡す                         |
| 終了時                       | 内容が変わっていれば、失敗した場合も含めて書き戻し、一時ファイルを消す |
| ログ                         | コマンドの `--cookies` の値を `<cookies>` に置き換える                 |
| プロファイルのファイルが無い | 実行せず、ログイン要求の文言で失敗させる                               |

### D-6: 失敗の記録 (FR-11, FR-12)

```python
def record_failure(key, auth_profile, output, url):
    hincrby(key, "failed_count", 1)
    extra = {"error": output, "failed_at": now, "error_code": "", "login_url": ""}
    if cookies.classify_login_required(output):
        extra["error_code"] = "login_required"
        profile = auth_profile or cookies.resolve_profile(url)   # 未指定は URL から求める
        extra["login_url"] = cookies.login_url(profile, url) or ""
        if auth_profile:
            cookies.mark_expired(redis, auth_profile)
    return jobs.update_status(redis, key, "failed", extra, REDIS_TTL)
```

| ジョブ記録の項目 | 内容                                                            |
| ---------------- | --------------------------------------------------------------- |
| `auth_profile`   | プロファイル名（未指定は空）                                    |
| `error_code`     | `login_required`、`interrupted`、または空（前回の値を残さない） |
| `login_url`      | 操作 URL の設定があるときの再ログイン先。無ければ空             |
| `worker_id`      | 実行中のワーカー                                                |

## 3. エラー処理

| 事象                           | 処理                                           |
| ------------------------------ | ---------------------------------------------- |
| Redis に接続できない（起動時） | エラーを出して終了コード 1 で終了する          |
| 取得中の Redis エラー          | エラーを出して終了コード 1 で終了する          |
| 解析できないジョブ             | 退避用のキューへ移し、終了コード 0 で終了する  |
| 所有者が別のワーカー           | 警告を出し、結果を書き込まずに終了する         |
| yt-dlp の失敗                  | 失敗として記録する（D-6）                      |
| yt-dlp が見つからない          | 失敗として記録する                             |
| リースの更新失敗               | 警告を出す。失敗が有効期間続けば実行を中止する |
