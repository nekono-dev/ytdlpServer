# タスク（全体）

各アプリの詳細は、そのアプリの `tasks.md` を一次情報とする。

## 状態一覧

| 機能 | 対応アプリ | 備考 |
|---|---|---|
| cookie セッション認証（Phase 1: cookie の利用とログイン要求の通知） | api, worker | 検証完了・コミット済み |
| cookie セッション認証（Phase 2: ブラウザログインによる cookie 自動取得） | browser, api, worker | 検証完了 |
| cookie セッション認証（Phase 3: 画面配信方式への置き換え） | browser | 検証完了。製品版での実スマートフォン操作は未確認 |
| cookie セッション認証（Phase 4: プロファイルのプリセット・履歴・自動選択） | browser, api, worker | 検証完了。実アカウントでの YouTube・Instagram・X・Bilibili は未確認 |

## 横断タスク

- [ ] 運用規則文書（AGENTS.md 相当）にコミット規則を明記する（機能単位で 1 commit、実機検証後に commit）。リポジトリには該当文書が無い（`.github/copilot-instructions.md` はある）ため、置き場所を決める。

## 積み残し

作業中に見つけた、現在の作業範囲外の課題。

### Redis Insight が認証なしで公開される

- 現象: `docker-compose*.yml` の `redis-insight`（5540）は認証が無く、Redis の全キー（ジョブの `options` や `error` を含む）が LAN から見える。
- 箇所: `docker-compose.yml` の `redis-insight` サービス。
- 修正案: 既定では起動しない（`profiles`）か、`127.0.0.1` に限定する。README の記載も合わせる。
- 発見: 2026-09-25、コード調査時。

### 予約実行で途中失敗すると、取り出した予約が失われる

- 現象: `/download/scheduled` は予約を `lpop` で取り出してから処理する。401 以外の失敗（probe 失敗など）では、取り出した予約が戻らず失われる。
- 箇所: `apiServer/src/main.py` の `download_scheduled`。
- 修正案: 失敗した予約を先頭へ戻す（401 では対応済み）。
- 発見: 2026-09-25、Phase 1 の実装時（401 のみ範囲内として対応）。

### `.github/copilot-instructions.md` が実装と乖離している

- 現象: エンドポイントが `/ytdlp` と書かれているなど、現在の API（`/download` ほか）と合っていない。
- 箇所: `.github/copilot-instructions.md`。
- 修正案: 現行の API 仕様へ書き直すか、`specs/` への参照に置き換える。
- 発見: 2026-09-25。
