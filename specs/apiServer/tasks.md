# タスク（apiServer）

## cookie セッション認証（Phase 1、検証完了）

設計は [design.md](design.md)。

- [x] `src/cookies.py` を追加（プロファイル検証、禁止オプション、一時コピーと書き戻し、状態、ログイン要求の判定）
- [x] `parse_request` に `auth_profile` を追加し、禁止オプションを 400 にする
- [x] probe（`function.probe_and_build_jobs`）で cookie を使い、ログイン要求を `LoginRequiredError` にする
- [x] `handle_download` でログイン要求を 401 にする（未登録・失効は probe せず返す。プロセスは再起動しない）
- [x] `/schedule`・`/download/scheduled` に `auth_profile` を通し、401 の予約を戻す
- [x] `GET /auth/profiles` を追加
- [x] compose（3 種）に `./cookies:/cookies` を追加、`.gitignore` に `/cookies/`
- [x] 単体テスト（`tests/test_cookies.py`、`tests/test_api.py`）
- [x] README・DEVELOP.md を更新

検証: 検証サーバ（Ubuntu 24.04、Docker Compose）で、ニコニコのログイン必須動画を使い実施。cookie なし・未登録・無効 cookie・禁止オプションの 401/400、有効 cookie での取得（mp4 1.4MB）、cookie の 0600 での書き戻し、ログインと Redis に cookie の値が無いことを確認。単体テストは全体で 34 件成功。
既知の未検証事項: ニコニコ以外のサイトのエラー文、YouTube。

## cookie セッション認証（Phase 2、検証完了）

設計は [design.md](design.md)。ログイン画面は [../browserServer/](../browserServer/) が提供する。

- [x] `cookies.login_url(profile, url)` を `BROWSER_UI_URL` と `start_url` から組み立てる
- [x] 401 応答と `GET /auth/profiles` の `login_url` に反映する
- [x] 単体テスト（`login_url` の組み立て、エンコード、未設定時の `null`、`http(s)` 以外の URL）
- [x] compose に `BROWSER_UI_URL`（`${BROWSER_UI_URL:-}`）を追加する
- [x] 実サイトのプロファイルで、失効（`expired`）から cookie の置き直しで再起動なしに復帰できることを確認する

検証: 検証サーバで、無効 cookie とプロファイル未指定の 401 が、`login_url`（`profile` と `start_url`、エンコード済み）を返すこと、`GET /auth/profiles` の各行が `?profile=` の URL を返すことを確認。

## cookie セッション認証（Phase 4、検証完了）

設計は [design.md](design.md)。

- [x] `cookies.py` にプロファイルの定義の読み込み（プリセット・履歴）と `resolve_profile()` を追加（3 アプリ同一）
- [x] `src/presets.json` を追加（3 アプリ同一）
- [x] `handle_download` で、`auth_profile` 省略時の自動選択（`select_profile`）と、候補での 401
- [x] `GET /auth/profiles` に `label` と `domains` を付ける
- [x] 単体テスト

検証は [../browserServer/tasks.md](../browserServer/tasks.md) の Phase 4 を参照（API の自動選択を含めて実施）。

## イベント駆動の worker 起動と yt-dlp の更新方式の見直し（実装・検証完了）

要件は [requirements.md](requirements.md)（A12〜A16）、設計は [design.md](design.md)。実機検証は [../workerServer/tasks.md](../workerServer/tasks.md) と合わせて実施した。

- [x] `push_jobs` の `RPUSH` の後に `queued` を通知する（失敗しても受付は成功）
- [x] `/download/retry` の更新後に `queued` を通知する
- [x] probe の失敗（`RuntimeError`）で、`os._exit` を廃止し、`check_update` を通知して 400 を返す
- [x] `/download/retry` を項目単位の `HSET` に変え、存在しなかったキーへの誤生成を検知して削除する
- [x] `entrypoint.sh` から `pip install --upgrade` と `SERVER_TTL` のタイマーを除く。`Dockerfile` に導入先の `PATH`・イメージ同梱の版・プラグインの置き場を設ける（ビルドコンテキストをリポジトリ直下に変更し、`workerServer/src/updater.py` を共有する）
- [x] `requirements.txt` から `yt-dlp[curl_cffi]` を除く
- [x] compose 3 種: `SERVER_TTL` を除き、`ytdlp-bin` を読み取り専用でマウントする
- [x] 単体テスト（通知の発行、通知失敗時に受付が成功すること、probe 失敗で再起動しないこと。`tests/test_api.py` の `os._exit` のモックを置き換えた）

検証: 検証サーバの Docker Compose で、probe 失敗時に 400 と `check_update` 通知（再起動しない）、`/download` 成功時の `queued` 通知、api コンテナが `ytdlp-bin` ボリューム（読み取り専用）から dispatcher の更新した yt-dlp を参照できることを確認した。`/download/retry` の項目単位更新は単体テストで確認（実機での競合再現は未実施）。
