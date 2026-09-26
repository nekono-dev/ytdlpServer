"""cookie プロファイル(セッション認証)の共通処理。

このファイルは apiServer/src と workerServer/src に同一内容で配置する
(with_youtube_defaults と同様の重複実装。変更時は両方を揃えること)。

cookie は共有ボリューム(COOKIE_DIR)に <profile>.txt(Netscape 形式)で保管する。
yt-dlp は cookie ファイルへ書き戻すため、実行時は一時コピーを渡し、
変更があった場合のみ原子的にストアへ反映する。
"""
from __future__ import annotations

import contextlib
import os
import re
import tempfile
import time
from pathlib import Path
from typing import TYPE_CHECKING
from urllib.parse import quote, urlencode, urlsplit

if TYPE_CHECKING:
    from collections.abc import Iterator

    from redis import Redis

COOKIE_DIR = Path(os.environ.get("COOKIE_DIR", "/cookies"))
BROWSER_UI_URL = os.environ.get("BROWSER_UI_URL", "").rstrip("/")
PROFILE_KEY_PREFIX = "ytdlp:auth:profile"
PROFILE_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,64}$")

# ログイン情報・cookie をユーザ指定の options で上書き/参照されないよう拒否する。
# (--netrc-cmd は任意コマンドを実行するため特に危険)
FORBIDDEN_LONG_OPTIONS = frozenset({
    "--cookies", "--cookies-from-browser",
    "--username", "--password", "--twofactor",
    "--netrc", "--netrc-location", "--netrc-cmd",
})
# 短縮形: -u(username) -p(password) -n(netrc)。値連結形(-uNAME)も対象。
FORBIDDEN_SHORT_OPTIONS = frozenset("upn")

MISSING_PREFIX = "login required: cookie profile not found"


class LoginRequiredError(RuntimeError):
    """yt-dlp がログイン(有効な cookie)を要求した場合の例外。"""


class ProfileNotFoundError(LoginRequiredError):
    """指定された cookie プロファイルのファイルが存在しない。"""


def validate_profile(name: object) -> str:
    if not isinstance(name, str) or not PROFILE_PATTERN.match(name):
        msg = "auth_profile must match [A-Za-z0-9_-]{1,64}"
        raise ValueError(msg)
    return name


def cookie_path(profile: str) -> Path:
    return COOKIE_DIR / f"{validate_profile(profile)}.txt"


def save_profile(profile: str, data: bytes) -> Path:
    """cookie を <profile>.txt へ保存する(0600、一時ファイル経由の置き換え)。

    読み手(yt-dlp を動かす API/Worker)に書きかけの内容を見せない。
    """
    dst = cookie_path(profile)
    COOKIE_DIR.mkdir(parents=True, exist_ok=True)
    staging = dst.with_name(f".{dst.name}.tmp")
    fd = os.open(staging, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "wb") as f:
        f.write(data)
    staging.chmod(0o600)  # 既存の一時ファイルが残っていた場合も権限を揃える
    staging.replace(dst)
    return dst


def check_options(options: list[str]) -> None:
    """認証情報・cookie に関する yt-dlp オプションが含まれていれば ValueError。"""
    for opt in options:
        if opt.startswith("--"):
            forbidden = opt.split("=", 1)[0] in FORBIDDEN_LONG_OPTIONS
        else:
            forbidden = (
                opt.startswith("-") and len(opt) > 1
                and opt[1] in FORBIDDEN_SHORT_OPTIONS)
        if forbidden:
            name = opt.split("=", 1)[0] if opt.startswith("--") else opt[:2]
            msg = (
                f"Option {name} is not allowed. "
                "Use auth_profile (cookie login) instead.")
            raise ValueError(msg)


def mask_command(cmd: list[str]) -> list[str]:
    """ログ出力用に --cookies の値を伏せる。"""
    masked: list[str] = []
    hide = False
    for arg in cmd:
        masked.append("<cookies>" if hide else arg)
        hide = arg == "--cookies"
    return masked


def classify_login_required(text: str | None) -> bool:
    """yt-dlp のエラー出力がログイン要求かを判定する。

    文言はサイト/バージョンで変わるため部分一致で判定する。
    実測(niconico):
      cookie なし: "Sensitive content, login required. Use --cookies, ..."
      cookie 無効: "Invalid session, re-login required"
    """
    t = (text or "").lower()
    return (
        "login required" in t
        or "use --cookies-from-browser or --cookies for the authentication" in t
        or "sign in to confirm" in t
        or "cookies are no longer valid" in t
        or MISSING_PREFIX in t)


@contextlib.contextmanager
def cookie_file(profile: str | None) -> Iterator[str | None]:
    """profile の cookie を一時コピーして yt-dlp へ渡すパスを返す。

    終了時、yt-dlp が cookie を更新していればストアへ原子的に書き戻す。
    profile が None の場合は None を返すだけ。
    """
    if not profile:
        yield None
        return

    src = cookie_path(profile)
    try:
        original = src.read_bytes()
    except FileNotFoundError:
        msg = f"{MISSING_PREFIX}: {profile}"
        raise ProfileNotFoundError(msg) from None

    fd, tmp = tempfile.mkstemp(prefix="cookies-", suffix=".txt")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(original)
        yield tmp
        # 正常終了・yt-dlp 失敗のどちらでも、更新があれば反映する(下の finally)
    finally:
        try:
            updated = Path(tmp).read_bytes()
            if updated and updated != original:
                save_profile(profile, updated)
        except OSError as e:
            print("WARNING: Failed to write back cookies:", e)
        finally:
            Path(tmp).unlink(missing_ok=True)


def _profile_key(profile: str) -> str:
    return f"{PROFILE_KEY_PREFIX}:{profile}"


def profile_state(client: Redis | None, profile: str) -> str:
    """`missing` / `expired` / `valid` を返す。

    expired は Redis の失効記録より新しい cookie ファイルが置かれるまで続く
    (cookie を入れ直せば自動で valid に戻る)。
    """
    try:
        mtime = cookie_path(profile).stat().st_mtime
    except FileNotFoundError:
        return "missing"
    if client is not None:
        try:
            data = client.hgetall(_profile_key(profile)) or {}
            expired_at = float(data.get("expired_at", 0))
            if data.get("status") == "expired" and expired_at >= mtime:
                return "expired"
        except Exception as e:
            print("WARNING: Failed to read profile state:", e)
    return "valid"


def mark_expired(client: Redis | None, profile: str) -> None:
    if client is None:
        return
    try:
        client.hset(_profile_key(profile), mapping={
            "status": "expired", "expired_at": str(time.time())})
    except Exception as e:
        print("WARNING: Failed to mark profile expired:", e)


def list_profiles(client: Redis | None) -> list[dict]:
    """プロファイル名・状態・更新時刻の一覧(cookie の中身は含めない)。"""
    if not COOKIE_DIR.is_dir():
        return []
    results: list[dict] = []
    for path in sorted(COOKIE_DIR.glob("*.txt")):
        name = path.stem
        if not PROFILE_PATTERN.match(name):
            continue
        results.append({
            "profile": name,
            "status": profile_state(client, name),
            "updated_at": path.stat().st_mtime,
            "login_url": login_url(name),
        })
    return results


def login_url(profile: str | None, url: str | None = None) -> str | None:
    """再ログイン用画面の URL。BROWSER_UI_URL が未設定なら None。

    profile があれば入力済みにする。url があれば、その origin を開始 URL にする。
    """
    if not BROWSER_UI_URL:
        return None
    params: dict[str, str] = {}
    if profile:
        params["profile"] = profile
    parts = urlsplit(url or "")
    if parts.scheme in ("http", "https") and parts.netloc:
        params["start_url"] = f"{parts.scheme}://{parts.netloc}/"
    query = urlencode(params, quote_via=quote)
    return f"{BROWSER_UI_URL}/?{query}" if query else f"{BROWSER_UI_URL}/"
