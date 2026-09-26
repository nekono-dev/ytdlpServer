"""CDP の cookie を、対象サイトのものに絞って Netscape 形式へ変換する。"""
from __future__ import annotations

from urllib.parse import urlsplit

import tldextract

# 同梱の Public Suffix List だけを使う(外部通信しない)
_EXTRACT = tldextract.TLDExtract(suffix_list_urls=())


def site_domain(start_url: str) -> str:
    """開始 URL の登録ドメイン(例: account.nicovideo.jp -> nicovideo.jp)。

    IP アドレスや localhost など登録ドメインが無い場合は、ホスト名そのもの。
    """
    host = (urlsplit(start_url).hostname or "").lower()
    return _EXTRACT(host).top_domain_under_public_suffix or host


def filter_cookies(cookies: list[dict], domains: list[str]) -> list[dict]:
    """cookie のドメインが、domains のいずれかと同じか、その配下のものだけを残す。"""
    bases = [d.lower().lstrip(".") for d in domains if d]
    kept: list[dict] = []
    for c in cookies:
        domain = str(c.get("domain", "")).lstrip(".").lower()
        if any(domain == b or domain.endswith(f".{b}") for b in bases):
            kept.append(c)
    return kept


def to_netscape(cookies: list[dict]) -> str:
    lines = ["# Netscape HTTP Cookie File"]
    for c in cookies:
        domain = c["domain"]
        include_sub = "TRUE" if domain.startswith(".") else "FALSE"
        expires = c.get("expires", -1)
        # セッション cookie は expires=0
        expires_s = "0" if c.get("session") or expires <= 0 else str(int(expires))
        if c.get("httpOnly"):
            domain = f"#HttpOnly_{domain}"
        lines.append("\t".join([
            domain, include_sub, c.get("path", "/"),
            "TRUE" if c.get("secure") else "FALSE",
            expires_s, c["name"], c["value"]]))
    return "\n".join(lines) + "\n"
