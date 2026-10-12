# yt-dlp 導入と更新 タスク一覧

## Phase 1: 選択と導入

- 対応: D-3, D-4, D-5
- [x] T-1.1: libc とアーキテクチャによるバイナリ名の選択を実装する。
- [x] T-1.2: 導入先の構成と、古い版の削除を実装する。
- [x] T-1.3: 初回導入と、同梱の版へのフォールバックを実装する。
- 検証: `node --test tests/spec-trace/ytdlp-updater.test.mjs --test-name-pattern "AC-(9|10|11|12|13)\b"`

## Phase 2: 確認と更新

- 対応: D-1, D-2, D-6
- [x] T-2.1: 間隔と最短間隔による確認の可否を実装する。
- [x] T-2.2: チェックサムと版の検証、原子的な切り替えを実装する。
- [x] T-2.3: Redis への記録と、環境変数による設定を実装する。
- 検証: `node --test tests/spec-trace/ytdlp-updater.test.mjs`
