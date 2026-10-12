# ワーカー タスク一覧

## Phase 1: 取得と所有者

- 対応: D-1, D-2
- [x] T-1.1: リトライ対象の原子的な取得と、キューからの取り出しを実装する。
- [x] T-1.2: 解析できないジョブの退避を実装する。
- [x] T-1.3: リースの更新と、所有者の確認、リースの失効による中止を実装する。
- 検証: `node --test tests/spec-trace/worker-runtime.test.mjs --test-name-pattern "AC-(1|2|3|4|5|6|9|22)\b"`

## Phase 2: 実行と停止

- 対応: D-3, D-4, D-5, D-6
- [x] T-2.1: 出力先の組み立てと、YouTube の既定値の補完を実装する。
- [x] T-2.2: 保存名の競合回避と、選んだ名前の記録を実装する。
- [x] T-2.3: cookie の一時コピーによる実行と、ログの伏せ字を実装する。
- [x] T-2.4: 停止の指示による yt-dlp の停止と、中断の記録を実装する。
- 検証: `node --test tests/spec-trace/worker-runtime.test.mjs --test-name-pattern "AC-(7|8|10|11|12|13|14|15|16|17|21)\b"`

## Phase 3: 失敗の記録

- 対応: D-7
- [x] T-3.1: ログイン要求の失敗の記録と、プロファイルの失効を実装する。
- [x] T-3.2: 通常の失敗の記録を実装する。
- 検証: `node --test tests/spec-trace/worker-runtime.test.mjs`
