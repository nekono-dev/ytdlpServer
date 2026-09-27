---
name: bootstrap-ci-builder
description: 単独で動作するブートストラップ型インストーラ（install.sh、curl経由でも実行可能）と、それをブランチpush時はCIアーティファクト・Pre-releaseとして・タグpush時はGitHub Releaseとして自動生成・頒布するGitHub Actionsワークフローを新規に整備する際に使う。手動実行（workflow_dispatch）やPre-releaseの再作成にも対応する。「install.shを作りたい」「頒布用のインストーラを作りたい」「ワンライナーでインストールできるようにしたい」「installerワークフローを作りたい」といった依頼で使う。
---

# ブートストラップ型インストーラ・CI整備

Linuxサーバ等のベアメタルへ「1行のcurl|shコマンド」で導入できる、単独動作のインストーラ（`install.sh`）と、それをブランチ・タグへのpushごとに自動生成・頒布するGitHub Actionsワークフローを整備するための作業方式。`templates/`配下のひな形を対象プロジェクトの実情に合わせて調整し、配置する。

## 成果物の構成（ファイルレイアウト）

対象プロジェクトの `install/` 配下（プロジェクトの慣習に合わせてディレクトリ名は変えてよいが、以下の3ファイルの役割分担は変えない）:

| ファイル | 役割 |
|---|---|
| `install/install.sh.tmpl` | 頒布される`install.sh`のひな形。`@@REF@@`・`@@COMMIT@@`・`@@REPO_URL@@`が未置換のプレースホルダとして残る。git等の導入・リポジトリの取得（clone/fetch）・取得先が本体インストーラ（`install/setup.sh`）へ引数をそのまま渡して実行するだけの薄い層。 |
| `install/build-install.sh` | `install.sh.tmpl`へ`REF`・`COMMIT`・`REPO_URL`を埋め込み、標準出力へ`install.sh`を出す生成スクリプト。CIと手元の両方から同じ手順で使う。 |
| `install/setup.sh` | 実際の導入・アンインストール処理を行う本体インストーラ。プロジェクト固有のものがある場合は、 `setup.sh` に置き換えること。`--uninstall` フラグに対応させ、本体の導入物のアンインストール処理を行うこと（`install.sh` 側のDIR自己削除の対象外）。 |
| `.github/workflows/installer.yml` | ブランチpushでは`install.sh`をCIアーティファクトとして保存しつつ、ブランチ名を元にしたタグ（`/`を`-`に置換。例`dev-v1.3`）でGitHub Pre-releaseへも添付する（pushのたびに既存Pre-releaseを削除して作り直す）。タグ（`v*`）pushではGitHub Releaseへ添付する。`workflow_dispatch`による手動実行にも対応し、ブランチを選んで再実行すればPre-releaseの再作成として使える。 |

**重要**: 頒布される最終成果物のファイル名は常に`install.sh`で固定する（`bootstrap.sh`のような別名にしない）。ひな形ファイル自体の名前を`install.sh.tmpl`とし、本体インストーラは`setup.sh` に固定することで、「ひな形」「本体」「生成物」の3者が同名になる混同を避ける。

## アンインストール

`install.sh` は引数に `--uninstall` を含む場合、`{{SETUP_ENTRY}} --uninstall` を実行した後、取得先ディレクトリ（`DIR`）自体を削除する。
`{{SETUP_ENTRY}}`（プロジェクト固有の本体インストーラ）は `--uninstall` フラグに対応させ、本体の導入物のアンインストールを行うこと。

## 着手前に確認する事項

ユーザーの作業方式（推測で進めず、不明点は着手前に解消する）に従い、以下が不明な場合は実装前に必ず確認する。

1. **リポジトリの取得元URL**（GitHub `owner/repo`。ワークフロー内では`github.repository`から組み立てられるため、通常は追加確認不要）。
2. **対象OS・パッケージマネージャ**（既定はDebian系＝`apt-get`。異なる場合はテンプレートの依存導入部を書き換える）。
3. **`install.sh.tmpl`が導入すべき最小限の依存**（git・ca-certificates以外に、取得後すぐに必要なものがあるか。本体インストーラ用の依存は本体側で入れるのが基本で、ひな形側は「取得するために最低限必要なもの」に留める）。
4. **既定の取得先ディレクトリ**（例`/opt/<アプリ名>`）と、環境変数での上書き手段の名前（例`<APPNAME大文字>_DIR`）。
5. **CIで実行すべき検査**（シェル構文・shellcheck以外に、そのプロジェクト固有の単体テスト・lintがあるか。既存の`npm test`等のCIステップがあれば流用する）。
6. **アーティファクト名・Release資産名の命名規則**（既存プロジェクトに慣習があれば合わせる。無ければCIアーティファクト名は`installer-<ブランチ名を/→-に置換>`、ブランチ用Pre-releaseのタグ名はプレフィックス無しの`<ブランチ名を/→-に置換>`（例`dev-v1.3`）を既定とする）。

## 作業手順

1. 上記「着手前に確認する事項」を解消する。
2. `templates/install.sh.tmpl`・`templates/build-install.sh`・`templates/installer-workflow.yml`を対象プロジェクトの`install/`・`.github/workflows/`へコピーし、テンプレート内の`{{PLACEHOLDER}}`をすべて実値に置換する（プレースホルダの一覧は各テンプレートのコメントを参照）。`@@REF@@`・`@@COMMIT@@`・`@@REPO_URL@@`は`install.sh.tmpl`内に**置換せず残す**（`build-install.sh`がCI実行時・ローカル実行時に置換する対象のため）。
3. ローカルで生成を検証する: `sh install/build-install.sh <REF> <40桁コミットハッシュ> <REPO_URL> > /tmp/install.sh && sh -n /tmp/install.sh`。可能なら`shellcheck -S warning`も通す。
4. `.env`等の追跡外ファイルが無いクリーンな取得先で、生成した`install.sh`を実際に実行し、導入→再実行（冪等性）を検証する。
6. ワークフローファイルの構文を確認する（`actionlint`があれば使う。無ければYAMLとしての構文だけでも確認する）。

## 各テンプレートのプレースホルダ

- `templates/install.sh.tmpl`: `{{APP_NAME}}`（アプリ識別子。ディレクトリ名・変数名に使う）、`{{DEFAULT_INSTALL_DIR}}`（既定の取得先。例`/opt/{{APP_NAME}}`）、`{{DIR_ENV_VAR}}`（取得先を上書きする環境変数名）、`{{OS_PACKAGE_MANAGER_CHECK}}`・`{{BOOTSTRAP_DEPS_INSTALL}}`（対象OS・パッケージマネージャに応じた依存導入コマンド）、`{{SETUP_ENTRY}}`（本体インストーラの相対パス。既定`install/setup.sh`）。
- `templates/build-install.sh`: `{{TEMPLATE_FILENAME}}`（既定`install.sh.tmpl`）。
- `templates/installer-workflow.yml`: `{{PRECHECK_STEPS}}`（シェル構文検査・shellcheck以外の、プロジェクト固有の検査ステップ群）、`{{APP_NAME}}`、`{{DIST_DIR}}`（既定`dist`）。

置換後、テンプレート由来である旨のコメント（例:「このスキルのテンプレートを元に生成」等の自己言及）は本文に残さないこと（README同様、成果物自体には執筆過程の注釈を書かない）。
