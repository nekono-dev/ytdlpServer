# ログイン用ブラウザ タスク一覧

## Phase 1: 起動とログイン操作の状態

- 対応: D-1, D-2, D-3
- [x] T-1.1: Chromium と Xvfb の起動、終了、ブラウザのプロファイルの破棄を実装する。
- [x] T-1.2: 起動引数と、タイムゾーンの警告を実装する。
- [x] T-1.3: 同時1件の状態管理、時間切れ、待機中に生成しない動作を実装する。
- 検証: `node --test tests/spec-trace/browser-server.test.mjs --test-name-pattern "AC-(2|5|9|10|11|16|18)\b"`

## Phase 2: 保存とプロファイル

- 対応: D-4, D-5, D-8
- [x] T-2.1: cookie の絞り込みと Netscape 形式への変換を実装する。
- [x] T-2.2: 保存、0件のときの継続、新しいサイトの履歴への追加を実装する。
- [x] T-2.3: プロファイル一覧と履歴の削除を実装する。
- 検証: `node --test tests/spec-trace/browser-server.test.mjs --test-name-pattern "AC-(1|3|4|12|13|15|17)\b"`

## Phase 3: 配信と入力

- 対応: D-6, D-7
- [x] T-3.1: 表示領域に合わせた配信と、フレームの応答を実装する。
- [x] T-3.2: タッチ・マウス・キー・文字入力・戻る・再読み込みの変換を実装する。
- [x] T-3.3: 新しいタブへの追従と、閉じたときの切り替えを実装する。
- 検証: `node --test tests/spec-trace/browser-server.test.mjs --test-name-pattern "AC-(6|7|8)\b"`

## Phase 4: 操作ページ

- 対応: D-9
- [x] T-4.1: プロファイルの選択、新しいサイトの入力欄、保存状況の表示を実装する。
- [x] T-4.2: クエリのプロファイル名と開始 URL を反映する。
- 検証: `node --test tests/spec-trace/browser-server.test.mjs --test-name-pattern "AC-14\b"`
- E2E: `tests/e2e/browser-server.spec.ts` クエリ付きで操作ページを開く → プロファイルが選択され、未知のサイトでは開始 URL が入力欄に入る
