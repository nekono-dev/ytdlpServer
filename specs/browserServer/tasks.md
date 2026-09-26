# タスク（browserServer）

## cookie セッション認証（Phase 2）

設計は [design.md](design.md)。

### Step 1: 実現性の検証（完了）

- [x] Chromium + Xvfb + x11vnc + noVNC のコンテナを検証サーバで構築し、LAN から noVNC が使えることを確認
- [x] CDP（`Storage.getCookies`）で、HttpOnly・セッション cookie を含めて回収できることを確認
- [x] 回収した cookie を Netscape 形式にして、yt-dlp の `--cookies` で使えることを確認
- [x] 人手でニコニコにログインし、その cookie で、ログインが必要な動画を取得できることを確認

検証: 検証サーバ（Ubuntu 24.04、Docker）で PoC を実施。モックのログインサイトで cookie なしは 403・ありは成功。ニコニコは `/my` と `nvapi.nicovideo.jp/v1/users/me` がログイン済み、ログイン必須動画（cookie なしは `Sensitive content, login required`）を音声形式で取得できた。Google のログイン画面も表示できた。
既知の未検証事項: noVNC 経由の入力の自動確認（サーバに Node が無いため。人手のログイン操作で実質確認済み）、Google のログイン成功と cookie の寿命、セッション cookie（expires=0）を yt-dlp が送ること。

### Step 2: 実装

- [ ] `cookies.py` に `save_profile()` を追加し、3 アプリで同一にする（テストで一致を確認）
- [ ] `browserServer/`（Dockerfile、`entrypoint`、`requirements.txt`、`src/`）を作成
- [ ] セッション管理（起動・状態・タイムアウト・破棄・排他）
- [ ] cookie の回収・登録ドメインでの絞り込み・Netscape 形式への変換・保存
- [ ] 制御 API と操作画面（noVNC の埋め込み、保存・キャンセル、残り時間、クエリの反映）
- [ ] compose（3 種）に `browser` サービスと、api・worker の `BROWSER_UI_URL` を追加
- [ ] 単体テスト（変換、絞り込み、状態遷移、排他、タイムアウト、保存の権限と原子的な置き換え、Chromium と CDP は差し替え）
- [ ] README（ブラウザでのログイン手順）・DEVELOP.md を、確定した仕様で更新

### Step 3: 実機検証（Step 2 の完了後にまとめて実施）

- [ ] モックのログインサイトで、開始 → ログイン → 保存 → `cookies/<名前>.txt` の内容と 0600 を確認 → yt-dlp で取得
- [ ] ニコニコで、人手ログイン → 保存 → ログインが必要な動画を取得
- [ ] 失効させた cookie で 401 → `login_url` を開く → 再ログイン → 保存 → サーバ再起動なしで 200
- [ ] 対象外ドメインの cookie が保存されないこと
- [ ] 同時開始が 409、タイムアウトで Chromium とプロファイルが消えること
- [ ] 待機中のメモリ・CPU、ログと Redis に cookie の値が無いこと

## 積み残し

- ログイン完了の自動検知（サイトごとの「ログイン済みを示す cookie 名」を指定し、現れたら保存する）。要否は未決。
- プロファイルの削除（操作画面・API）。
- Alpine インストーラ（Docker 無し構成）への browserServer の組み込み。
