"""去重：键 = language_code + normalized + kind + pos（不按 surface）。"""
from __future__ import annotations


def dedup_key(language_code: str, normalized: str, kind: str, pos: str) -> str:
    return "\u0000".join([
        (language_code or "").strip().lower(),
        (normalized or "").strip(),
        (kind or "").strip().lower(),
        (pos or "").strip().lower(),
    ])


def dedup_entries(entries: list) -> list:
    seen = set()
    out = []
    for e in entries:
        k = dedup_key(e.get("language_code", ""), e.get("normalized", ""),
                      e.get("kind", ""), e.get("pos", ""))
        if k in seen:
            continue
        seen.add(k)
        out.append(e)
    return out
