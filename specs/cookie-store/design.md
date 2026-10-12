# cookie ストア 設計書

## 1. 構成

| パス                                     | 責務                                              |
| ---------------------------------------- | ------------------------------------------------- |
| `apiServer/src/cookies.py`               | cookie ストアの共通処理（API 用の複製）           |
| `workerServer/src/cookies.py`            | cookie ストアの共通処理（ワーカー用の複製）       |
| `browserServer/src/cookies.py`           | cookie ストアの共通処理（ブラウザ用の複製）       |
| `apiServer/src/presets.json`             | プリセットのプロファイル定義（3アプリで同一内容） |
| `tests/test_cookies.py`                  | cookie 処理の単体テスト                           |
| `tests/spec-trace/cookie-store.test.mjs` | 受け入れ条件のタグ付きで単体テストを実行する      |

## 2. 設計項目

### D-1: ファイルの形式と更新 (FR-1, FR-2)

| 項目           | 内容                                                           |
| -------------- | -------------------------------------------------------------- |
| 場所           | 環境変数 COOKIE_DIR（既定 `/cookies`）                         |
| ファイル名     | `<プロファイル名>.txt`（Netscape 形式）                        |
| プロファイル名 | 正規表現 `^[A-Za-z0-9_-]{1,64}$`                               |
| 権限           | ファイル 600                                                   |
| 更新           | 同じディレクトリの `.<名前>.txt.tmp` へ 600 で書き、置き換える |

```python
def save_profile(profile: str, data: bytes) -> Path:
    dst = cookie_path(profile)          # 名前を検証してパスを返す
    staging = dst.with_name(f".{dst.name}.tmp")
    fd = os.open(staging, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "wb") as f:
        f.write(data)
    staging.chmod(0o600)
    staging.replace(dst)                # 原子的な置き換え
    return dst
```

### D-2: 状態の判定 (FR-3, FR-4)

| 状態   | 条件                                                   |
| ------ | ------------------------------------------------------ |
| 未保存 | ファイルが無い                                         |
| 失効   | Redis の記録が失効で、記録時刻がファイルの更新時刻以後 |
| 有効   | 上記以外                                               |

失効の記録は Redis の hash `ytdlp:auth:profile:<名前>` に持つ。Redis を読めないときは有効として扱う。

```python
def profile_state(client, profile) -> str:
    mtime = cookie_path(profile).stat().st_mtime      # 無ければ "missing"
    data = client.hgetall(f"ytdlp:auth:profile:{profile}")
    if data.get("status") == "expired" and float(data["expired_at"]) >= mtime:
        return "expired"
    return "valid"
```

### D-3: 一時コピーと書き戻し (FR-5)

```python
@contextlib.contextmanager
def cookie_file(profile: str | None) -> Iterator[str | None]:
    # profile が None なら None を返すだけ
    original = cookie_path(profile).read_bytes()      # 無ければ ProfileNotFoundError
    tmp = write_temp(original)                        # mkstemp の一時ファイル
    try:
        yield tmp
    finally:
        updated = Path(tmp).read_bytes()
        if updated and updated != original:           # 変更があるときだけ反映する
            save_profile(profile, updated)
        Path(tmp).unlink(missing_ok=True)
```

呼び出し側が例外を出しても、`finally` で書き戻す。

### D-4: オプションの拒否と文言の判定 (FR-6, FR-7, FR-11, NFR-2)

| 種類           | 対象                                                                                                                    |
| -------------- | ----------------------------------------------------------------------------------------------------------------------- |
| 長い形の拒否   | `--cookies` `--cookies-from-browser` `--username` `--password` `--twofactor` `--netrc` `--netrc-location` `--netrc-cmd` |
| 短い形の拒否   | `-u` `-p` `-n`（`-uNAME` のような値連結形を含む）                                                                       |
| エラー文       | オプション名だけを含め、値を含めない                                                                                    |
| コマンドの伏せ | `--cookies` の次の引数を `<cookies>` に置き換える                                                                       |

ログイン要求の判定は、小文字化した出力に次のいずれかが含まれるかで行う。

| 部分文字列                                                       |
| ---------------------------------------------------------------- |
| `login required`                                                 |
| `use --cookies-from-browser or --cookies for the authentication` |
| `sign in to confirm`                                             |
| `cookies are no longer valid`                                    |
| `login required: cookie profile not found`（未保存の通知）       |

### D-5: 一覧の項目 (FR-8)

```json
[
  {
    "profile": "nico",
    "label": "ニコニコ",
    "domains": ["nicovideo.jp"],
    "status": "valid",
    "updated_at": 1790000000.0,
    "login_url": "http://host:8080/?profile=nico"
  }
]
```

ファイルが存在するプロファイルだけを列挙し、cookie の中身は含めない。ラベルと対象ドメインは定義から付け、定義が無ければ名前と空の配列にする。

### D-6: プロファイル定義と自動選択 (FR-9, FR-10)

| 種類       | 置き場所                               | 書き手             |
| ---------- | -------------------------------------- | ------------------ |
| プリセット | `presets.json`（3アプリで同一）        | リポジトリ         |
| 履歴       | `COOKIE_DIR/profiles.json`（権限 600） | ログイン用ブラウザ |

```python
def profile_entries() -> list[dict]:
    presets = load_presets()                     # 定義順
    names = {e["name"] for e in presets}
    return presets + [e for e in load_history() if e["name"] not in names]

def resolve_profile(url: str | None) -> str | None:
    host = urlsplit(url or "").hostname
    return next((e["name"] for e in profile_entries()
                 if host and host_matches(host, e["domains"])), None)

def host_matches(host: str, domains: list[str]) -> bool:
    host = host.lower().rstrip(".")
    return any(host == d or host.endswith("." + d) for d in domains)
```

履歴を読めない、または項目が不正なときは警告を出して空として扱う。

### D-7: 再ログイン用 URL (FR-12)

```python
def login_url(profile: str | None, url: str | None = None) -> str | None:
    if not BROWSER_UI_URL:
        return None
    params = {}
    if profile:
        params["profile"] = profile
    parts = urlsplit(url or "")
    if parts.scheme in ("http", "https") and parts.netloc:
        params["start_url"] = f"{parts.scheme}://{parts.netloc}/"
    query = urlencode(params, quote_via=quote)
    return f"{BROWSER_UI_URL}/?{query}" if query else f"{BROWSER_UI_URL}/"
```

### D-8: 複製の同一性 (NFR-1)

| 項目     | 内容                                                         |
| -------- | ------------------------------------------------------------ |
| 複製     | 3つの `cookies.py` と3つの `presets.json` を、同一内容で置く |
| 検査     | 単体テストが、複製どうしのバイト列の一致を比べる             |
| 変更手順 | 1つを編集したら、他の2つへコピーする                         |

## 3. エラー処理

| 事象                           | 処理                               |
| ------------------------------ | ---------------------------------- |
| 名前が規則に合わない           | 値エラーにする                     |
| 指定プロファイルのファイル無し | ログイン要求の例外（未保存）を出す |
| 書き戻しの失敗                 | 警告を出し、一時ファイルを削除する |
| Redis の読み書きの失敗         | 警告を出し、状態は有効として扱う   |
| 履歴ファイルの破損             | 警告を出し、履歴を空として扱う     |
