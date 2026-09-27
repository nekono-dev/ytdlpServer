# タスク（workerServer）

## cookie セッション認証（Phase 1、検証完了）

設計は [design.md](design.md)。

- [x] `src/cookies.py` を追加（apiServer と同一内容）
- [x] `run_yt_dlp` で cookie の一時コピーを使い、書き戻す。ログのコマンドを伏せる
- [x] 失敗時に、ログイン要求なら `error_code=login_required` を記録し、プロファイルを `expired` にする（`record_failure`）
- [x] ログイン要求で失敗したジョブを、自動リトライの対象から外す（`is_waiting_for_login`）
- [x] `auth_profile` をジョブ hash に保持し、再試行時のジョブに引き継ぐ
- [x] compose（3 種）に `./cookies:/cookies` を追加
- [x] Alpine インストーラに `COOKIE_DIR` を追加
- [x] 単体テスト（`tests/test_worker.py`）

検証: 検証サーバ（Ubuntu 24.04、Docker Compose）で、有効 cookie の取得（mp4 1.4MB）、キュー投入後に失効した cookie のジョブが `error_code=login_required` で失敗し、再試行されないこと、ログに cookie の値・パスが出ないことを確認。
既知の未検証事項: Alpine インストーラ（`sh -n` の構文確認のみ）。

## cookie セッション認証（Phase 2、検証完了）

設計は [design.md](design.md)。

- [x] `login_url` を、ジョブの URL の origin を `start_url` にして組み立てる（`record_failure`）
- [x] 単体テスト（`test_record_failure_login_url`）
- [x] compose に `BROWSER_UI_URL`（`${BROWSER_UI_URL:-}`）を追加する

検証: 単体テスト。実機では、キュー投入後に失効したジョブの `login_url` は未確認（API 側の 401 と同じ組み立てを共用）。

## cookie セッション認証（Phase 4、検証完了）

設計は [design.md](design.md)。

- [x] `record_failure` で、`auth_profile` の無いジョブの `login_url` にプロファイルを使う
- [x] 単体テスト

検証: 単体テスト。自動選択されたプロファイルがジョブ hash の `auth_profile` に入ることを実機で確認（`ytdlp:jobs:completed:auto` の `auth_profile=mockhist`）。

## イベント駆動の worker 起動、yt-dlp の更新、ライフサイクル（設計済み・未実装）

要件は [requirements.md](requirements.md)（W7〜W30）、設計は [design.md](design.md)。

### Step 0: 方式の検証（PoC、完了）

yt-dlp の更新方式を比較するため、検証サーバ（Ubuntu 24.04）の Alpine 3.21 コンテナで、pip 方式と GitHub Releases のバイナリ方式を比較した。

- [x] 更新確認・取得のコスト（HEAD、API、pypi.org の JSON、バイナリ・zipapp の取得とチェックサム）
- [x] 起動時間（`--version` の 10 回平均）、実行時の `/tmp` の使用量
- [x] 機能（curl-cffi の impersonate、EJS と node の検出、bgutil プラグインの読み込み。設定ファイルの `--plugin-dirs` と標準の置き場 `/etc/yt-dlp/plugins`）
- [x] YouTube の probe（2 動画 × 各 8 回、pip 版と交互）と実ダウンロード（ffmpeg でマージ）
- [x] `yt-dlp -U`、実行中のダウンロードをまたぐシンボリックリンクの切替、破損したバイナリの検出

結果と採用の判断は [../design.md](../design.md) の「yt-dlp の配布方式」。既知の未検証事項: aarch64 の実行（資産の存在のみ確認）、実機の Alpine（LXC）、YouTube 以外のサイト、cookie を使った取得。

### Step 1: 実装（完了）

- [x] `jobs.py`: 状態遷移（`RENAME`）、取得（`RENAMENX`・`LMOVE`）、リース、回収、リトライ対象の判定（既存の関数を移す）
- [x] `main.py`: 新しい取得の手順、ハートビートのスレッド、停止の指示（SIGTERM・SIGINT）、所有者の確認、`pending` の廃止。設計時になかった追加: リースを更新できない状態が `LEASE_TTL` 続いたら、SIGTERM を受けたときと同じ経路で実行を中止する（W25）
- [x] `function.py`: `run_yt_dlp` を `Popen` にし、停止の指示を子プロセスへ伝える
- [x] `updater.py`: 新版の確認・取得・検証・切替・古い版の削除、Redis への記録、`check_update` のクールダウン
- [x] `dispatcher.py`: 通知の購読・定期スキャン・起動数の計算・バックオフ・回収の呼び出し。設計時の「起動猶予（START_GRACE）」は、採用した起動先（`backends.py`）がいずれも起動と同時に実行中の数へ反映されるため、実装では省略した（`running_count()` が起動直後の worker も直ちに数える）
- [x] `backends.py`（新規）: worker の起動先を抽象化。`ProcessBackend`（Alpine。子プロセス、終了の検知、停止時の SIGTERM 伝播）、`DockerBackend`（Compose。Docker API、`volumes_from`、`network_mode=container:`、ラベル、数え直し、停止時の `stop`、`docker events` による終了の検知）
- [x] `Dockerfile`: 導入先（`/opt/ytdlp`）・イメージ同梱の版（`/opt/ytdlp-image`、チェックサム検証）・プラグイン（`/etc/yt-dlp/plugins/bgutil`）・`PATH`・dispatcher の起動コマンド。`requirements.txt` から `yt-dlp` を除く
- [x] compose 3 種: `worker` を `dispatcher` に置き換え、`docker-proxy`・`ytdlp-bin` を追加。ビルドコンテキストをリポジトリ直下に変更（`updater.py` を api・worker で共有するため）
- [x] Alpine インストーラ: `ytdlp-dispatcher` の登録、`ytdlp-worker.*` の削除、`WORKER_COUNT` → `WORKER_MAX`、yt-dlp の初期版の導入、`update-ytdlp` と `run-api` の `timeout` の廃止、プラグインの導入
- [x] 単体テスト（起動数の計算、更新の条件、回収の判定、状態遷移の競合、停止の指示）。`tests/test_worker.py`（jobs・function・main）、`tests/test_dispatcher.py`（updater・backends・dispatcher）

実装中に見つかり、設計と異なる形にした点:
- docker-socket-proxy には `EVENTS=1` も許可する（`docker events` で worker の終了を検知するため。設計時は `CONTAINERS`・`POST` のみとしていた）。
- worker コンテナの起動で、自コンテナの `image` プロパティ（`.image`）は使わない。docker-py がこれを解決する際に追加で IMAGES API を要求し、許可していないため 403 になる。`attrs["Image"]`（画像 ID）を直接使う。
- `containers.run()` は `stop_timeout` を受け付けない（docker-py の対応キーワード外）。停止は `stop_all()` が毎回 `timeout` を明示して `stop()` を呼ぶ形で管理し、生成時には渡さない。

### Step 2: ドキュメント（完了）

- [x] README（`--scale worker=N` → `WORKER_MAX`、`SERVER_TTL` の廃止、Alpine の設定表、`probe failed` の節）、DEVELOP（worker・dispatcher・更新・Redis データ構造・API の再起動の節）を更新した

### Step 3: 実機検証（完了。一部は単体テストのみ）

検証環境: 検証サーバ（Ubuntu 24.04）。Docker Compose（`docker-proxy`・`EVENTS=1` を含む）、および同サーバの LXD コンテナ（Alpine 3.21）へ `install-alpine.sh` を実行して確認した。

起動（Compose・Alpine とも確認）:
- [x] 待機中に worker が存在しないこと（Compose: worker コンテナ 0、Alpine: `main.py` の子プロセス 0）
- [x] ジョブ投入から worker 起動・完了まで（数秒以内。YouTube 実動画のダウンロードで確認）
- [x] `WORKER_MAX=2` で 2 ジョブが並列に処理されること（Compose、2 つの worker コンテナが同時に存在することを確認）
- [x] dispatcher の再起動後、実行中の worker を数え直すこと（Compose。`docker kill` で dispatcher を落とし、生存する 2 台の worker を再カウントし、追加起動しないことを確認）
- [x] docker-socket-proxy が、許可外の API（`images.list` など IMAGES 系）を拒否すること
- [ ] 待機中の通信が新版の確認（GitHub）だけであること: worker・API 双方の起動時通信の設計上の根拠（コード上 Redis と GitHub 以外に通信しない）は確認したが、パケットキャプチャ等での直接確認は行っていない
- [ ] 通知を止めても、定期スキャンでジョブが処理されること: 未確認（`DISPATCH_SCAN_INTERVAL` を短くしての再現は今回省略）
- [ ] 自動リトライ・`/download/retry` で worker が起動すること: 自動リトライ（中断後の再取得）は確認した。`/download/retry` エンドポイント経由の起動は単体テストのみ

更新:
- [x] 新版が無いとき、取得しないこと。新版があるとき、取得・適用され、api と worker が再起動なしに新版を使うこと（Compose: `None → 2026.08.19` の初回適用、以後 `up to date` を確認。api コンテナが同じボリュームの版を参照できることも確認）
- [ ] 破損した取得物（チェックサム不一致）を適用しないこと、GitHub に到達できないときのフォールバック、導入先が空のときの動作: 単体テスト（`test_dispatcher.py`）のみ。実機での意図的な障害注入は未実施
- [ ] 実行中のジョブが、切替をまたいで完走すること: yt-dlp の更新方式の PoC（Step 0）で、シンボリックリンクの切替をまたいだ完走を確認済み。今回の実装（`updater.py`）そのものでの再現は未実施
- [x] probe の失敗で `check_update` が通知されること（実機でログイン要求以外の probe 失敗から 400 応答と通知を確認）。クールダウンでの抑制は単体テストのみ

ライフサイクル:
- [x] SIGTERM で、ジョブが `interrupted` になり、`failed_count` が増えないこと（`docker stop` で worker コンテナへ実機で確認）。dispatcher の停止でも同様（dispatcher の再起動時、実行中の worker が SIGTERM を受けて `interrupted` になり、再試行されることを確認）
- [ ] `kill -9`・`docker kill` で worker を止めたジョブが回収され再実行されること（`failed_count` が +1）: `jobs.reclaim_stale` の単体テストで確認。実機での `kill -9` 単独の再現は未実施
- [ ] `LMOVE` 直後・`in_progress` の作成前に止めても、ジョブが失われないこと: 単体テストのみ
- [ ] 2 つの worker が同じ `failed` のジョブを取り合っても、片方だけが取得すること: 単体テスト（`take_retryable_failed`）のみ。実機での意図的な競合再現は未実施
- [ ] リースの更新を止めた worker が、自ら停止し、完了を記録しないこと: 実装は追加した（上記）が、単体テスト・実機検証とも未実施
- [ ] 中断したダウンロードが、`.part` から再開されること（ffmpeg のマージの途中の中断を含む）: 未検証
- [ ] 毎回異常終了するジョブが、`RETRY_COUNT` で止まること: 未検証（ロジック上は `record_failure`／回収時の `failed_count` 加算と `RETRY_COUNT` 判定の組み合わせで従来どおり成立する）

既知の未検証事項（積み残しではなく、この機能の検証範囲として次回以降に回すもの）: 上記のチェックが付いていない項目。
