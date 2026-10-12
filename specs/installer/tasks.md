# ytdlpServer インストーラ タスク一覧

## Phase 1: 骨格と設定

- 対応: D-2, D-3, D-10
- [x] T-1.1: ホスト側スキルのひな形から bootstrap.sh.tmpl・build-bootstrap.sh・main.sh・config.schema を配置する。
- [x] T-1.2: config.schema に全設定キーを宣言する。
- [x] T-1.3: app.sh に check_env と check_values を実装する。
- [x] T-1.4: 旧 install ディレクトリと旧ワークフローを削除する。
- 検証: `sh -n installer/main.sh && node --test tests/installer/installer.test.mjs --test-name-pattern "AC-(2|3|4|5|14)"`

## Phase 2: コアの導入とサービス

- 対応: D-1, D-8, D-11
- [x] T-2.1: install_packages に apt の依存と NodeSource の Node.js 24 の導入を実装する。
- [x] T-2.2: setup_python、setup_ytdlp、setup_pot を実装する。
- [x] T-2.3: setup_dirs と setup_redis を実装する。
- [x] T-2.4: installer/run のラッパーと installer/units のユニットを作成する。
- [x] T-2.5: setup_units と start_core を実装する。
- 検証: `node --test tests/installer/installer.test.mjs --test-name-pattern "AC-(1|12|15|16)"`

## Phase 3: 任意機能

- 対応: D-5
- [x] T-3.1: ログイン用ブラウザの導入と撤去を実装する。
- [x] T-3.2: nginx の導入と撤去を実装する。
- [x] T-3.3: cloudflared の導入と撤去を実装する。
- [x] T-3.4: Redis の管理ツールの導入と撤去を実装し、Redis Insight の失敗時の継続を実装する。
- 検証: `node --test tests/installer/installer.test.mjs --test-name-pattern "AC-(7|8)"`

## Phase 4: 更新・撤去・状態

- 対応: D-4, D-6, D-7
- [x] T-4.1: app_uninstall に全ユニットと配置物の削除を実装する。
- [x] T-4.2: app_purge に Redis のデータと cookie の保存先の削除を実装する。
- [x] T-4.3: update でスキーマの新キーが追記されることを確認する。
- 検証: `node --test tests/installer/installer.test.mjs --test-name-pattern "AC-(6|9|10|11)"`

## Phase 5: CI と文書

- 対応: D-9
- [x] T-5.1: release-installer.yml を配置し、検査ステップに受け入れ検証を組み込む。
- [x] T-5.2: README と DEVELOP をホスト側スキルの方式に合わせて更新する。
- [x] T-5.3: 空の Ubuntu 24.04 コンテナで全受け入れ条件を実行する。
- 検証: `node --test tests/installer/installer.test.mjs`
