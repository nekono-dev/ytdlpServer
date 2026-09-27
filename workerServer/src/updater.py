"""yt-dlp の新版の確認・取得・検証・原子的な切替 (dispatcher の別スレッドで実行)。

設計は specs/workerServer/design.md の「yt-dlp の更新」を参照。
GitHub Releases の musllinux バイナリを使う (PoC の比較結果、specs/design.md 参照)。
署名 (SHA2-256SUMS.sig) の検証は行わない (specs/workerServer/design.md の既知の制約)。
"""
from __future__ import annotations

import hashlib
import os
import platform
import re
import shutil
import subprocess
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

UPDATER_HASH_KEY = "ytdlp:updater"
RELEASE_TIMEOUT = 30
DOWNLOAD_TIMEOUT = 120


@dataclass(frozen=True)
class UpdaterConfig:
    ytdlp_dir: Path
    repo: str = "yt-dlp/yt-dlp"
    update_interval: int = 21600
    update_retry_interval: int = 1800
    update_cooldown: int = 1800
    keep_versions: int = 2

    @classmethod
    def from_env(cls) -> UpdaterConfig:
        return cls(
            ytdlp_dir=Path(os.environ.get("YTDLP_DIR", "/opt/ytdlp")),
            repo=os.environ.get("YTDLP_REPO", "yt-dlp/yt-dlp"),
            update_interval=int(os.environ.get("UPDATE_INTERVAL", "21600")),
            update_retry_interval=int(
                os.environ.get("UPDATE_RETRY_INTERVAL", "1800")),
            update_cooldown=int(os.environ.get("UPDATE_COOLDOWN", "1800")),
            keep_versions=int(os.environ.get("KEEP_VERSIONS", "2")),
        )


def asset_name() -> str:
    machine = platform.machine().lower()
    if machine in ("x86_64", "amd64"):
        return "yt-dlp_musllinux"
    if machine in ("aarch64", "arm64"):
        return "yt-dlp_musllinux_aarch64"
    msg = f"unsupported architecture for yt-dlp binary: {machine}"
    raise RuntimeError(msg)


def latest_tag(repo: str) -> str:
    """`releases/latest` へのリダイレクト先からタグ名を得る (API は使わない。レート制限のため)。"""
    url = f"https://github.com/{repo}/releases/latest"
    req = urllib.request.Request(url, method="HEAD")
    with urllib.request.urlopen(req, timeout=RELEASE_TIMEOUT) as resp:
        final_url = resp.geturl()
    return final_url.rsplit("/", 1)[-1]


def _download(url: str, dest: Path) -> None:
    req = urllib.request.Request(url, headers={"User-Agent": "ytdlpServer-updater"})
    with urllib.request.urlopen(req, timeout=DOWNLOAD_TIMEOUT) as resp, \
            open(dest, "wb") as f:
        shutil.copyfileobj(resp, f)


def _sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _verify_checksum(binary: Path, sums_text: str, name: str) -> bool:
    digest = _sha256_of(binary)
    pattern = re.compile(rf"^{re.escape(digest)}\s+\*?{re.escape(name)}$", re.M)
    return pattern.search(sums_text) is not None


def _installed_version(ytdlp_path: Path) -> str | None:
    try:
        proc = subprocess.run(
            [str(ytdlp_path), "--version"], capture_output=True, text=True,
            timeout=30, check=False)
    except Exception:
        return None
    if proc.returncode != 0:
        return None
    return proc.stdout.strip() or None


def _prune_old_versions(versions_dir: Path, keep: int, current_version: str) -> None:
    if not versions_dir.is_dir():
        return
    entries = sorted(
        (p for p in versions_dir.iterdir() if p.is_dir()),
        key=lambda p: p.stat().st_mtime, reverse=True)
    kept = 0
    for p in entries:
        if p.name == current_version:
            kept += 1
            continue
        if kept < keep:
            kept += 1
            continue
        shutil.rmtree(p, ignore_errors=True)


def fetch_and_apply(cfg: UpdaterConfig, tag: str) -> None:
    """指定した版を取得・検証し、`current` を原子的に切り替える。失敗すれば例外を送出する。"""
    name = asset_name()
    base = f"https://github.com/{cfg.repo}/releases/download/{tag}"
    versions_dir = cfg.ytdlp_dir / "versions"
    dest_dir = versions_dir / tag
    dest_dir.mkdir(parents=True, exist_ok=True)
    binary_tmp = dest_dir / f".{name}.part"
    sums_tmp = dest_dir / ".SHA2-256SUMS.part"
    try:
        _download(f"{base}/{name}", binary_tmp)
        _download(f"{base}/SHA2-256SUMS", sums_tmp)
        sums_text = sums_tmp.read_text(encoding="utf-8", errors="replace")
        if not _verify_checksum(binary_tmp, sums_text, name):
            msg = f"checksum mismatch for {name} ({tag})"
            raise RuntimeError(msg)
        binary_path = dest_dir / "yt-dlp"
        binary_tmp.chmod(0o755)
        binary_tmp.replace(binary_path)

        installed = _installed_version(binary_path)
        if installed != tag:
            msg = (
                f"downloaded yt-dlp reports version {installed!r}, "
                f"expected {tag!r}")
            raise RuntimeError(msg)

        current_tmp = cfg.ytdlp_dir / ".current.tmp"
        current_link = cfg.ytdlp_dir / "current"
        rel_target = Path("versions") / tag
        if current_tmp.exists() or current_tmp.is_symlink():
            current_tmp.unlink()
        current_tmp.symlink_to(rel_target)
        current_tmp.replace(current_link)  # os.rename: 原子的な切替
    finally:
        binary_tmp.unlink(missing_ok=True)
        sums_tmp.unlink(missing_ok=True)

    _prune_old_versions(versions_dir, cfg.keep_versions, tag)


def check_and_update(
        redis_client, cfg: UpdaterConfig, *, force: bool = False) -> None:
    """必要なら新版を確認・適用する。例外は送出せず、Redis に結果を記録する。"""
    now = time.time()
    try:
        state = redis_client.hgetall(UPDATER_HASH_KEY) or {}
    except Exception as e:
        print("WARNING: Failed to read updater state:", e)
        state = {}

    checked_at = float(state.get("checked_at") or 0)
    last_error_at = float(state.get("error_at") or 0)

    if force:
        if now - checked_at < cfg.update_cooldown:
            return
    elif checked_at:
        due = cfg.update_retry_interval if last_error_at >= checked_at \
            else cfg.update_interval
        if now - checked_at < due:
            return

    print("INFO: Checking for a new yt-dlp release...")
    try:
        tag = latest_tag(cfg.repo)
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        print("WARNING: Failed to check yt-dlp releases:", e)
        _record(redis_client, checked_at=now, error=str(e))
        return

    current_link = cfg.ytdlp_dir / "current"
    current_version = None
    if current_link.exists():
        current_version = _installed_version(current_link / "yt-dlp")

    if current_version == tag:
        print("INFO: yt-dlp is up to date:", tag)
        _record(redis_client, checked_at=now, current=tag, error=None)
        return

    print(f"INFO: Updating yt-dlp {current_version!r} -> {tag!r}")
    try:
        fetch_and_apply(cfg, tag)
    except Exception as e:
        print("WARNING: Failed to update yt-dlp:", e)
        _record(redis_client, checked_at=now, error=str(e))
        return

    print("INFO: yt-dlp updated to", tag)
    _record(
        redis_client, checked_at=now, current=tag, updated_at=now, error=None)


def _record(
        redis_client, *, checked_at: float, current: str | None = None,
        updated_at: float | None = None, error: str | None = None) -> None:
    mapping = {"checked_at": str(checked_at)}
    if current is not None:
        mapping["current"] = current
    if updated_at is not None:
        mapping["updated_at"] = str(updated_at)
    if error is not None:
        mapping["last_error"] = error
        mapping["error_at"] = str(checked_at)
    else:
        mapping["last_error"] = ""
    try:
        redis_client.hset(UPDATER_HASH_KEY, mapping=mapping)
    except Exception as e:
        print("WARNING: Failed to record updater state:", e)


def ensure_initial_version(cfg: UpdaterConfig) -> None:
    """導入先が空のとき、初回の版を取得する (イメージビルド時・Alpine インストール時に使う)。

    失敗した場合は例外を送出する (呼び出し側でビルド・インストールを失敗させる)。
    """
    current_link = cfg.ytdlp_dir / "current"
    if current_link.exists():
        return
    tag = latest_tag(cfg.repo)
    fetch_and_apply(cfg, tag)
    print("INFO: yt-dlp initial version installed:", tag)


if __name__ == "__main__":
    # インストーラ・Dockerfile のビルド時に、初回の版を取得するために呼ぶ。
    ensure_initial_version(UpdaterConfig.from_env())
