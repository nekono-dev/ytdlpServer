# yt-dlp バイナリ選択 設計書

## 1. 構成

| パス                                | 責務                                          |
| ----------------------------------- | --------------------------------------------- |
| `workerServer/src/updater.py`       | libc とアーキテクチャに応じたバイナリ名の選択 |
| `tests/test_updater_asset.py`       | バイナリ名の選択の単体テスト                  |
| `tests/ytdlp-binary/asset.test.mjs` | 単体テストを受け入れ条件のタグ付きで実行      |

## 2. 設計項目

### D-1: バイナリ名の選択 (FR-1, FR-2, FR-3)

```python
def is_musl() -> bool:
    # musl 環境には /lib/ld-musl-* が存在する
    return any(Path("/lib").glob("ld-musl-*"))

def asset_name() -> str:
    machine = platform.machine().lower()
    arch = {"x86_64": "", "amd64": "", "aarch64": "_aarch64", "arm64": "_aarch64"}
    if machine not in arch:
        raise RuntimeError(f"unsupported architecture for yt-dlp binary: {machine}")
    base = "yt-dlp_musllinux" if is_musl() else "yt-dlp_linux"
    return base + arch[machine]
```

| libc  | x86_64           | aarch64                  |
| ----- | ---------------- | ------------------------ |
| glibc | yt-dlp_linux     | yt-dlp_linux_aarch64     |
| musl  | yt-dlp_musllinux | yt-dlp_musllinux_aarch64 |

## 3. エラー処理

| 事象                   | 処理                   |
| ---------------------- | ---------------------- |
| 未対応のアーキテクチャ | 実行時エラーで中止する |
