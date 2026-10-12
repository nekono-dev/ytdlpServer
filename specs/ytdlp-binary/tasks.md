# yt-dlp バイナリ選択 タスク一覧

## Phase 1: 選択処理

- 対応: D-1
- [x] T-1.1: updater の asset_name を libc 判定つきに改修する。
- [x] T-1.2: 単体テストを受け入れ条件のタグ付きで追加し、既存テストを新仕様に合わせる。
- [x] T-1.3: 親のワーカー仕様書にあるバイナリ選択の記述を、新しい選択規則に合わせる。
- 検証: `node --test tests/ytdlp-binary/asset.test.mjs`
