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

## cookie セッション認証（Phase 2、未着手）

設計は [design.md](design.md)。ログイン画面は [../browserServer/](../browserServer/) が提供する。

- [ ] `cookies.login_url(profile, url)` を `BROWSER_UI_URL` と `start_url` から組み立てる
- [ ] 401 応答と `GET /auth/profiles` の `login_url` に反映する
- [ ] 単体テスト（`login_url` の組み立て、エンコード、未設定時の `null`）
- [ ] compose に `BROWSER_UI_URL` を追加する
