# ytdlpServer インストーラ タスク一覧

## Phase 1: 骨格と設定

- 対応: D-2, D-3, D-10
- [x] T-1.1: ホスト側スキルのひな形から bootstrap.sh.tmpl・build-bootstrap.sh・main.sh・config.schema を配置する。
- [x] T-1.2: config.schema に全設定キーを宣言する。
- [x] T-1.3: app.sh の app_check に check_env と値の検査を実装し、値は config_value で読む。
- [x] T-1.4: 旧 install ディレクトリと旧ワークフローを削除する。
- [x] T-1.5: host-service-builder の sync.sh で installer/host-service.sh を配置する。
- 検証: `sh -n installer/main.sh && node --test tests/installer/installer.test.mjs --test-name-pattern "AC-(2|3|4|5|14)"`

## Phase 2: コアの導入とサービス

- 対応: D-1, D-8, D-11
- [x] T-2.1: install_packages に apt の依存と NodeSource の Node.js 24 の導入を実装する。
- [x] T-2.2: setup_python、setup_ytdlp、setup_pot を実装する。
- [x] T-2.3: setup_dirs に専用ユーザーの作成と保存先の所有者の設定を実装し、setup_redis を実装する。
- [x] T-2.4: installer/run のラッパーを、HS_DIR・HS_CONF_FILE・HS_DATA_DIR から導入先を読む形で作成する。
- [x] T-2.5: start_core を hs_service と hs_start で実装し、各ユニットを専用ユーザーで動かす。
- 検証: `node --test tests/installer/installer.test.mjs --test-name-pattern "AC-(1|12|15|16)"`

## Phase 3: 任意機能

- 対応: D-5
- [x] T-3.1: ログイン用ブラウザの導入と撤去を実装する。
- [x] T-3.2: nginx の導入と撤去を実装する。
- [x] T-3.3: cloudflared の導入と撤去を実装する。
- [x] T-3.4: Redis の管理ツールの導入と撤去を実装し、Redis Insight の失敗時の継続を実装する。
- [x] T-3.5: 無効にした任意機能のユニットを hs_prune で撤去する。
- 検証: `node --test tests/installer/installer.test.mjs --test-name-pattern "AC-(7|8)"`

## Phase 4: 更新・撤去・状態

- 対応: D-4, D-6, D-7
- [x] T-4.1: app_uninstall に hs_remove_all による全ユニットの撤去と、配置物の削除を実装する。
- [x] T-4.2: app_purge に Redis のデータ・cookie の保存先・専用ユーザーの削除を実装する。
- [x] T-4.3: update でスキーマの新キーが追記されることを確認する。
- 検証: `node --test tests/installer/installer.test.mjs --test-name-pattern "AC-(6|9|10|11)"`

## Phase 5: CI と文書

- 対応: D-9
- [x] T-5.1: release-installer.yml を配置し、検査ステップに受け入れ検証を組み込む。
- [x] T-5.2: README と DEVELOP をホスト側スキルの方式に合わせて更新する。
- [x] T-5.3: 空の Ubuntu 24.04 コンテナで全受け入れ条件を実行する。
- 検証: `node --test tests/installer/installer.test.mjs`
