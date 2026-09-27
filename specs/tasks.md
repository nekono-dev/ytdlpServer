# タスク（全体）

各アプリの詳細は、そのアプリの `tasks.md` を一次情報とする。

## 状態一覧

| 機能 | 対応アプリ | 備考 |
|---|---|---|
| cookie セッション認証（Phase 1: cookie の利用とログイン要求の通知） | api, worker | 検証完了・コミット済み |
| cookie セッション認証（Phase 2: ブラウザログインによる cookie 自動取得） | browser, api, worker | 検証完了 |
| cookie セッション認証（Phase 3: 画面配信方式への置き換え） | browser | 検証完了。製品版での実スマートフォン操作は未確認 |
| cookie セッション認証（Phase 4: プロファイルのプリセット・履歴・自動選択） | browser, api, worker | 検証完了。実アカウントでの YouTube・Instagram・X・Bilibili は未確認 |
| イベント駆動の worker 起動、yt-dlp の更新方式の見直し（バイナリの定期確認）、worker のライフサイクル | api, worker | 検証完了。一部項目は単体テストのみ（実機での障害注入・競合の再現は次回以降）。詳細は [workerServer/tasks.md](workerServer/tasks.md)・[apiServer/tasks.md](apiServer/tasks.md) |

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

### 同じ URL を短時間に複数回投入すると、ジョブの記録が競合する

- 現象: ジョブの Redis キーは yt-dlp の動画 ID から決まる。同じ URL（同じ動画）を、前回の処理が終わる前にもう一度投入すると、後発のジョブが同じキー（`ytdlp:jobs:in_progress:<id>` 等）を取得・上書きし、2 つの worker が同じ記録を奪い合う（`worker_id` の上書き、片方の完了記録の消失）。イベント駆動化・並列 worker（`WORKER_MAX`）により、同じキューに同じ URL が重ねて積まれる機会が増え、顕在化しやすくなった。
- 箇所: `apiServer/src/function.py`（ジョブ ID の採番）、`workerServer/src/jobs.py`（`commit_from_processing` が既存キーの有無を確認せず上書きする）。
- 修正案: `commit_from_processing` を、対象キーが既に存在する場合は取得を諦める（別のジョブ ID を振るか、キューへ戻す）ようにする。API 側で同一 URL の二重投入を検知する案もある。
- 発見: 2026-09-27、イベント駆動の worker 起動の実機検証時（検証サーバの Docker Compose で、同じ動画を連続投入して確認）。

