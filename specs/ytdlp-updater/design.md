# yt-dlp 導入と更新 設計書

## 1. 構成

| パス                                      | 責務                                                                          |
| ----------------------------------------- | ----------------------------------------------------------------------------- |
| `workerServer/src/updater.py`             | 確認、取得、検証、切り替え、削除、初回導入、Redis への記録                    |
| `workerServer/Dockerfile`                 | ビルド時に同梱の版を導入し、検索パスに導入先と同梱の版を並べる                |
| `apiServer/Dockerfile`                    | API イメージ。同じ updater で同梱の版を導入し、導入先を読み取り専用で参照する |
| `tests/test_updater.py`                   | 更新処理の単体テスト                                                          |
| `tests/test_updater_asset.py`             | バイナリ名の選択の単体テスト                                                  |
| `tests/test_dispatcher.py`                | 確認の間隔と最短間隔の単体テスト                                              |
| `tests/spec-trace/ytdlp-updater.test.mjs` | 受け入れ条件のタグ付きで単体テストを実行する                                  |

## 2. 設計項目

### D-1: 確認と取得の流れ (FR-1, FR-4, FR-5, NFR-1, NFR-2)

```text
check_and_update(redis, cfg, force=False)
  state = HGETALL ytdlp:updater
  force なら   now - checked_at < update_cooldown で何もしない
  force でなければ now - checked_at < (直前が失敗なら update_retry_interval、成功なら update_interval) で何もしない
  tag = HEAD https://github.com/<repo>/releases/latest の転送先の末尾
  tag が current の版と同じ → checked_at を記録して終了
  fetch_and_apply(cfg, tag)
  失敗 → last_error と error_at を記録、導入済みの版のまま
```

| 項目   | 内容                                                                                |
| ------ | ----------------------------------------------------------------------------------- |
| 確認   | HEAD 要求1回。API（api.github.com）と pypi.org は使わない                           |
| 実行   | dispatcher の別スレッドで行い、同時に1つだけ実行する                                |
| 契機   | 起動時、既定間隔ごと、新版確認の依頼（`check_update`）                              |
| 通信先 | 確認は github.com。取得時だけリダイレクト先（release-assets.githubusercontent.com） |

### D-2: 検証と原子的な切り替え (FR-2, FR-3)

```python
def fetch_and_apply(cfg, tag):
    name = asset_name()
    base = f"https://github.com/{cfg.repo}/releases/download/{tag}"
    dest = cfg.ytdlp_dir / "versions" / tag
    _download(f"{base}/{name}", dest / f".{name}.part")
    _download(f"{base}/SHA2-256SUMS", dest / ".SHA2-256SUMS.part")
    if not _verify_checksum(binary_tmp, sums_text, name):
        raise RuntimeError("checksum mismatch")
    binary_tmp.chmod(0o755); binary_tmp.replace(dest / "yt-dlp")
    if _installed_version(dest / "yt-dlp") != tag:        # --version が期待する版か
        raise RuntimeError("version mismatch")
    tmp = cfg.ytdlp_dir / ".current.tmp"
    tmp.symlink_to(Path("versions") / tag)
    tmp.replace(cfg.ytdlp_dir / "current")                # rename による原子的な切り替え
    _prune_old_versions(versions_dir, cfg.keep_versions, tag)
```

実行中のプロセスは旧版のファイルのまま完走し、新しく起動するものが新版を使う。旧版のファイルは保持数の範囲で削除しない。

### D-3: 導入先の構成と古い版の削除 (FR-7)

```text
<導入先>/
  versions/<版>/yt-dlp     取得した版
  current -> versions/<版>  使う版（相対リンク）
```

更新時刻の新しい順に、現在の版を含めて `KEEP_VERSIONS` 版を残し、それより古い版のディレクトリを削除する。

### D-4: バイナリの選択 (FR-8)

```python
def is_musl() -> bool:
    return any(Path("/lib").glob("ld-musl-*"))

def asset_name() -> str:
    machine = platform.machine().lower()
    suffix = {"x86_64": "", "amd64": "", "aarch64": "_aarch64", "arm64": "_aarch64"}
    if machine not in suffix:
        raise RuntimeError(f"unsupported architecture for yt-dlp binary: {machine}")
    return ("yt-dlp_musllinux" if is_musl() else "yt-dlp_linux") + suffix[machine]
```

| libc  | x86_64             | aarch64                    |
| ----- | ------------------ | -------------------------- |
| glibc | `yt-dlp_linux`     | `yt-dlp_linux_aarch64`     |
| musl  | `yt-dlp_musllinux` | `yt-dlp_musllinux_aarch64` |

チェックサムは同じ版の `SHA2-256SUMS` と照合する。

### D-5: 同梱の版と初回導入 (FR-9, FR-10)

| 項目     | 内容                                                                                                |
| -------- | --------------------------------------------------------------------------------------------------- |
| 検索パス | `<導入先>/current` を先頭、同梱の版（`/opt/ytdlp-image/current`）を後ろに並べる                     |
| 同梱の版 | イメージのビルド時に `updater.py` を実行して導入する。取得に失敗したらビルドを失敗させる            |
| 初回導入 | `ensure_initial_version` は、現在の版のリンクが無いときだけ最新の版を取得し、失敗時は例外を送出する |
| 呼び出し | イメージのビルドとインストーラが `python updater.py` を実行する                                     |

### D-6: 記録と設定 (FR-6, FR-11, NFR-2)

Redis の hash `ytdlp:updater` の項目は次のとおり。

| 項目         | 内容                     |
| ------------ | ------------------------ |
| `checked_at` | 前回の確認の時刻         |
| `current`    | 導入中の版               |
| `updated_at` | 前回の更新の時刻         |
| `last_error` | 直近の失敗（成功時は空） |
| `error_at`   | 直近の失敗の時刻         |

| 環境変数                | 既定            | 内容                           |
| ----------------------- | --------------- | ------------------------------ |
| `YTDLP_DIR`             | `/opt/ytdlp`    | 導入先                         |
| `YTDLP_REPO`            | `yt-dlp/yt-dlp` | 取得元の GitHub リポジトリ     |
| `UPDATE_INTERVAL`       | `21600`         | 確認の間隔（秒）               |
| `UPDATE_RETRY_INTERVAL` | `1800`          | 失敗後の再確認の間隔（秒）     |
| `UPDATE_COOLDOWN`       | `1800`          | 依頼による確認の最短間隔（秒） |
| `KEEP_VERSIONS`         | `2`             | 残す版の数                     |

## 3. エラー処理

| 事象                     | 処理                                                           |
| ------------------------ | -------------------------------------------------------------- |
| 確認の通信失敗           | 失敗を記録し、導入済みの版を使い続ける。再確認の間隔は短くする |
| チェックサムの不一致     | 取得物を破棄し、失敗を記録する                                 |
| 報告する版が期待と異なる | 適用せず、失敗を記録する                                       |
| Redis の読み書きの失敗   | 警告を出し、状態は空として扱う                                 |
| 未対応のアーキテクチャ   | 実行時エラーにする                                             |
| 初回導入の失敗           | 例外を送出し、呼び出し元のビルドまたはインストールを失敗させる |
