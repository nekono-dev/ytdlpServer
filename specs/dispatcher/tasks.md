# ワーカー起動役 タスク一覧

## Phase 1: 監視と起動

- 対応: D-1, D-2, D-3, D-8, D-9
- [x] T-1.1: 対象数の計算とログイン待ちの除外を実装する。
- [x] T-1.2: 監視ループ、通知の購読、新版確認の中継を実装する。
- [x] T-1.3: 起動数の算出と、起動失敗のバックオフを実装する。
- [x] T-1.4: 環境変数の設定と、旧名の同時実行数の別名を実装する。
- 検証: `node --test tests/spec-trace/dispatcher.test.mjs --test-name-pattern "AC-(1|2|3|4|6|15|16|17|18|19)\b"`

## Phase 2: 回収

- 対応: D-4
- [x] T-2.1: リースの無い処理中ジョブと取り出し中の記録の回収を実装する。
- [x] T-2.2: 所有者の記録が無い古い処理中ジョブの回収を実装する。
- 検証: `node --test tests/spec-trace/dispatcher.test.mjs --test-name-pattern "AC-(7|8|9|10)\b"`

## Phase 3: 起動先

- 対応: D-5, D-6, D-7
- [x] T-3.1: 子プロセスのバックエンドを実装する。
- [x] T-3.2: Docker のバックエンドと docker-socket-proxy の定義を実装する。
- [x] T-3.3: 停止時に起動したワーカーへ停止を指示する。
- 検証: `node --test tests/spec-trace/dispatcher.test.mjs`
