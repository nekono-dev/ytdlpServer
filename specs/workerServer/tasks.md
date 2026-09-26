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
