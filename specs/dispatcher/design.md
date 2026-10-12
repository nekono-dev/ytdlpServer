# ワーカー起動役 設計書

## 1. 構成

| パス                                   | 責務                                                             |
| -------------------------------------- | ---------------------------------------------------------------- |
| `workerServer/src/dispatcher.py`       | 監視ループ、通知の購読、起動数の決定、バックオフ、回収の呼び出し |
| `workerServer/src/backends.py`         | ワーカーの起動先（子プロセス、Docker コンテナ）                  |
| `workerServer/src/jobs.py`             | 対象数の計算と回収（ワーカーと共有するジョブ操作の一部）         |
| `workerServer/Dockerfile`              | 起動役とワーカーが共用する実行イメージ                           |
| `workerServer/entrypoint.sh`           | 起動役のコマンドの切り替え                                       |
| `docker-compose.yml`                   | 起動役と docker-socket-proxy の定義（派生の2ファイルも同じ構成） |
| `tests/test_dispatcher.py`             | 起動役とバックエンドの単体テスト                                 |
| `tests/test_dispatch_loop.py`          | 監視ループ、通知、Docker バックエンドの単体テスト                |
| `tests/test_worker.py`                 | 回収と対象数の単体テスト                                         |
| `tests/spec-trace/dispatcher.test.mjs` | 受け入れ条件のタグ付きで単体テストを実行する                     |

## 2. 設計項目

### D-1: 監視ループと起動数 (FR-1, FR-2, FR-3, FR-4)

```python
def _main_loop(self):
    while not self.stopping.is_set():
        self._consume_exit_codes()
        jobs.reclaim_stale(self.redis, inprogress_stale=..., ttl=...)
        if time.time() >= next_allowed_start:
            running = self.backend.running_count()
            free = max(0, worker_max - running)
            if free > 0:
                pending = jobs.count_pending_targets(self.redis, retry_count, cap=free)
                for _ in range(min(free, pending)):
                    self._start_one()
                if backoff := self._backoff_seconds():
                    next_allowed_start = time.time() + backoff
        self.wake.wait(timeout=scan_interval)      # 通知・終了検知・定期スキャンで起床
        self.wake.clear()
```

| 項目         | 内容                                                                                                          |
| ------------ | ------------------------------------------------------------------------------------------------------------- |
| 対象数       | キュー `ytdlp:queue` の長さ ＋ `ytdlp:jobs:failed:*` のうち失敗回数がリトライ回数未満でログイン待ちでないもの |
| ログイン待ち | 失敗の理由がログイン要求で、プロファイル未指定、またはプロファイルの状態が有効でないもの                      |
| 読む項目     | キューの長さと、ジョブ記録の `failed_count` `error_code` `auth_profile` `worker_id` `started_at` だけ         |
| 空振り       | 起動したワーカーがジョブを取れなかったときは、ワーカーがすぐ終了する                                          |

### D-2: 通知と起床 (FR-3, FR-11)

Redis のチャンネル `ytdlp:events` を購読し、切断されたら5秒後に再接続する。

| メッセージ     | 動作                                                         |
| -------------- | ------------------------------------------------------------ |
| `queued`       | 監視ループを起床させる                                       |
| `check_update` | yt-dlp の強制確認の要求を立てる（最短間隔は updater が判定） |
| その他         | 監視ループを起床させる                                       |

起床の契機は、通知、ワーカーの終了、定期スキャンのタイムアウトの3つである。

### D-3: 起動失敗のバックオフ (FR-5, NFR-2)

```python
BACKOFF_BASE, BACKOFF_MAX = 5, 60

def _backoff_seconds(self) -> float:
    if self._consecutive_failures <= 0:
        return 0
    return min(BACKOFF_MAX, BACKOFF_BASE * 2 ** (self._consecutive_failures - 1))
```

終了コードが 0 以外のワーカーを検知するたびに連続失敗を増やし、0 のワーカーを検知したら 0 に戻す。起動そのものに失敗したときも増やす。

### D-4: 回収 (FR-6, FR-7)

監視ループのたびに、ジョブ操作の回収を呼ぶ。

| 対象                        | 条件                                                  | 回収                                                                                     |
| --------------------------- | ----------------------------------------------------- | ---------------------------------------------------------------------------------------- |
| `ytdlp:processing:<worker>` | ワーカーのリースが無い                                | 要素の `id` の処理中記録があれば要素だけを消す。無ければキューの先頭へ戻す。リストを消す |
| `ytdlp:jobs:in_progress:*`  | 記録の `worker_id` のリースが無い                     | `failed` へ移し、`error_code=interrupted`、`failed_count` を1増やす                      |
| `ytdlp:jobs:in_progress:*`  | `worker_id` が無く、`started_at` から指定時間を過ぎた | 同上                                                                                     |

取り出し中の記録を先に処理する。処理中の記録の回収が先に済むと、同じワーカーの二重の記録をキューへ戻してしまうためである。ワーカーの終了を検知したときも監視ループを起床させるが、回収の判定は同じ条件で行う。

### D-5: 子プロセスのバックエンド (FR-9, FR-10)

```python
class ProcessBackend:
    def start_worker(self):
        proc = subprocess.Popen(self._cmd, env=self._env)
        self._procs[proc.pid] = proc
        threading.Thread(target=self._wait_for, args=(proc,), daemon=True).start()
    def running_count(self) -> int: return len(self._procs)
    def stop_all(self, timeout):             # terminate → 待機 → 超過で kill
        ...
```

終了したプロセスの終了コードを記録し、起床の合図を出す。ワーカー用の常駐サービスは登録しない。再起動の後の数え直しは不要である（子プロセスは起動役とともに終了する）。

### D-6: Docker のバックエンド (FR-4, FR-8, FR-10)

起動役は自身のコンテナを inspect して、次の内容でワーカーのコンテナを作る。

| 項目         | 値                                                                     |
| ------------ | ---------------------------------------------------------------------- |
| イメージ     | 自身のイメージ ID（イメージ API を使わない）                           |
| コマンド     | 固定 `["python3", "-u", "/workspace/main.py"]`                         |
| 環境変数     | 自身の環境変数                                                         |
| マウント     | `volumes_from=[自身]`                                                  |
| ネットワーク | `network_mode=container:<自身>`                                        |
| ラベル       | `ytdlp.role=worker`。実行中の数は、このラベルの一覧で数える            |
| 終了時       | `auto_remove=True`。終了イベントを購読して終了コードを記録し、起床する |

停止時はラベルの一覧に `stop(timeout)` を呼ぶ。docker-socket-proxy には `CONTAINERS=1` `POST=1` `EVENTS=1` だけを許可し、`DOCKER_HOST=tcp://docker-proxy:2375` で接続する。docker.sock は直接マウントしない。

### D-7: 停止 (FR-10)

```python
def run(self):
    try:
        self._main_loop()
    finally:
        self.backend.stop_all(timeout=self.cfg.stop_timeout)   # STOP_GRACE + 10 秒
```

SIGTERM と SIGINT で停止のフラグを立てて起床させる。

### D-8: 設定 (FR-12, NFR-1)

| 環境変数                 | 既定                      | 内容                                               |
| ------------------------ | ------------------------- | -------------------------------------------------- |
| `WORKER_MAX`             | `1`                       | ワーカーの最大数。未指定なら `WORKER_COUNT` を読む |
| `DISPATCH_SCAN_INTERVAL` | `30`                      | 定期スキャンの間隔（秒）                           |
| `LEASE_TTL`              | `60`                      | リースの有効期間（秒）                             |
| `INPROGRESS_STALE`       | `21600`                   | 所有者の記録が無い処理中ジョブを回収するまでの秒数 |
| `RETRY_COUNT`            | `5`                       | リトライの上限                                     |
| `STOP_GRACE`             | `20`                      | 停止の猶予（秒）。停止の待機はこれに10秒を足す     |
| `DISPATCH_MODE`          | `process`                 | `process` または `docker`                          |
| `DOCKER_HOST`            | `tcp://docker-proxy:2375` | Docker API の接続先                                |

### D-9: ジョブの内容を読まない (NFR-3)

起動役のコードは、ジョブの JSON を解析せず、ワーカーのコマンドにジョブの項目を含めない。

| 通信先              | 内容                                                                                                  |
| ------------------- | ----------------------------------------------------------------------------------------------------- |
| Redis               | キューの長さと、ジョブ記録の `failed_count` `error_code` `auth_profile` `worker_id` `started_at` だけ |
| docker-socket-proxy | ワーカーのコンテナの作成・起動・停止・一覧・イベント                                                  |
| GitHub              | 新版の確認（updater が行う）                                                                          |

## 3. エラー処理

| 事象                            | 処理                                       |
| ------------------------------- | ------------------------------------------ |
| Redis に接続できない（起動時）  | エラーを出して終了コード 1 で終了する      |
| 購読の切断                      | 警告を出し、5秒後に再接続する              |
| 回収または件数計算の失敗        | 警告を出し、次の周期で再試行する           |
| ワーカーの起動に失敗            | 連続失敗を増やし、バックオフする           |
| 実行中の数を数えられない        | 最大数だけ動いているとみなして起動を見送る |
| Docker のイベント購読が使えない | 警告を出し、定期スキャンだけで運用する     |
