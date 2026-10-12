# cookie ストア タスク一覧

## Phase 1: ファイルと状態

- 対応: D-1, D-2, D-3
- [x] T-1.1: 名前の検証と、置き換えによる保存を実装する。
- [x] T-1.2: Redis の失効記録によるプロファイル状態の判定を実装する。
- [x] T-1.3: 一時コピーの受け渡しと、失敗時を含む書き戻しを実装する。
- 検証: `node --test tests/spec-trace/cookie-store.test.mjs --test-name-pattern "AC-(1|2|3|4|5|6)\b"`

## Phase 2: オプションの拒否と定義

- 対応: D-4, D-5, D-6, D-7
- [x] T-2.1: 認証関連オプションの拒否、コマンドの伏せ、ログイン要求の判定を実装する。
- [x] T-2.2: プロファイル一覧と、再ログイン用 URL の組み立てを実装する。
- [x] T-2.3: プリセットと履歴の読み込み、URL からの自動選択を実装する。
- 検証: `node --test tests/spec-trace/cookie-store.test.mjs --test-name-pattern "AC-(7|8|9|10|11|12|13|14)\b"`

## Phase 3: 複製の同一性

- 対応: D-8
- [x] T-3.1: 3つの複製の一致を検査する単体テストを追加する。
- 検証: `node --test tests/spec-trace/cookie-store.test.mjs`
