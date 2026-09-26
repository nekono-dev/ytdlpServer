# タスク（browserServer）

## cookie セッション認証（Phase 2、検証完了）

設計は [design.md](design.md)。

### Step 1: 実現性の検証（完了）

- [x] Chromium + Xvfb + x11vnc + noVNC のコンテナを検証サーバで構築し、LAN から noVNC が使えることを確認
- [x] CDP（`Storage.getCookies`）で、HttpOnly・セッション cookie を含めて回収できることを確認
- [x] 回収した cookie を Netscape 形式にして、yt-dlp の `--cookies` で使えることを確認
- [x] 人手でニコニコにログインし、その cookie で、ログインが必要な動画を取得できることを確認

検証: 検証サーバ（Ubuntu 24.04、Docker）で PoC を実施。モックのログインサイトで cookie なしは 403・ありは成功。ニコニコは `/my` と `nvapi.nicovideo.jp/v1/users/me` がログイン済み、ログイン必須動画（cookie なしは `Sensitive content, login required`）を音声形式で取得できた。Google のログイン画面も表示できた。
既知の未検証事項: noVNC 経由の入力の自動確認（サーバに Node が無いため。人手のログイン操作で実質確認済み）、Google のログイン成功と cookie の寿命、セッション cookie（expires=0）を yt-dlp が送ること。

### Step 2: 実装（完了）

- [x] `cookies.py` に `save_profile()` を追加し、3 アプリで同一にする（テストで一致を確認）
- [x] `browserServer/`（Dockerfile、`requirements.txt`、`src/`）を作成
- [x] セッション管理（起動・状態・タイムアウト・破棄・排他）
- [x] cookie の回収・登録ドメインでの絞り込み・Netscape 形式への変換・保存
- [x] 制御 API と操作画面（noVNC の埋め込み、保存・キャンセル、残り時間、クエリの反映）
- [x] compose（3 種）に `browser` サービスと、api・worker の `BROWSER_UI_URL` を追加
- [x] 単体テスト（変換、絞り込み、状態遷移、排他、タイムアウト、保存の権限と原子的な置き換え、Chromium と CDP は差し替え）
- [x] README（ブラウザでのログイン手順）・DEVELOP.md を更新

### Step 3: 実機検証（完了）

- [x] モックのログインサイトで、開始 → ログイン → 保存 → `cookies/<名前>.txt` の内容と 0600 を確認 → API から yt-dlp で取得
- [x] ニコニコで、人手ログイン → 保存 → ログインが必要な動画を取得（操作画面から）
- [x] 失効させた cookie で 401 → `login_url` → cookie を置き直す → サーバ再起動なしで 200
- [x] 対象外ドメインの cookie が保存されないこと
- [x] 同時開始が 409、タイムアウトで Chromium とプロファイルが消えること
- [x] 待機中のメモリ・CPU、ログと Redis に cookie の値が無いこと

検証: 検証サーバ（Ubuntu 24.04、Docker Compose）で、実 Chromium を使い実施。
- モックのログインサイト: 保存された cookie は 3 件（HttpOnly・通常・セッション）で権限 0600。API の `/download` で、保存したプロファイルを使って取得できた。
- 対象外ドメイン: ブラウザ内に `.google.com` の cookie があっても、保存されるのは開始 URL のドメインのみ。
- 排他・終了: 稼働中の再開始は 409。`SESSION_TIMEOUT=15` で自動終了し、ブラウザ系プロセスとプロファイルが 0 件になる。
- リソース: 待機中は約 31MiB・CPU 0.01%・ブラウザ系プロセス 0。ログイン中は約 250MiB。イメージは 1.65GB。
- 到達性: 6080（noVNC）は 200 と RFB ハンドシェイクを返す。CDP（9222）・RFB（5900）は LAN から到達不可。
- 漏洩: cookie の値は、全サービスのログ・Redis 全キーで 0 件。
- ニコニコ（実サイト）: 操作画面から人手でログインして保存（cookie 9 件、`.nicovideo.jp` と `www.nicovideo.jp`）。保存したプロファイルで、ログイン必須動画（sm46845740）を取得できた（mp4 1.4MB）。ログと Redis に実 cookie の値は 0 件。
- 復帰: 失効中（`expired`）のプロファイルへ cookie を置き直すと、サーバ再起動なしで `valid` に戻り、同じリクエストが 200 になった。置き直しは `cp` で模擬した（実際の再ログインでの保存は、`nico` の新規保存で確認済み）。
- 操作画面: 待機中の画面（クエリの反映）とログイン中の画面（ボタン・残り時間・noVNC の埋め込み枠）を確認。ヘッドレスでは埋め込みの描画までは確認できていない。
既知の未検証事項: セッション cookie（expires=0）を yt-dlp が送ること、Google/YouTube。

## 積み残し

- ログイン完了の自動検知（サイトごとの「ログイン済みを示す cookie 名」を指定し、現れたら保存する）。要否は未決。
- プロファイルの削除（操作画面・API）。
- Alpine インストーラ（Docker 無し構成）への browserServer の組み込み。
