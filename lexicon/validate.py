"""条目校验。

- errors：阻断入库的硬错误。
- warnings：仅提示（如缺释义 / 例句），不阻断（wordfreq 等源可能先入库后补全）。
"""
from __future__ import annotations

from .cefr import is_valid_cefr

VALID_KINDS = {"vocabulary", "phrase", "sentence"}


def validate_entry(e: dict):
    errors, warnings = [], []
    for f in ("language_code", "surface", "normalized", "kind", "source"):
        if not (e.get(f) or "").strip():
            errors.append(f"missing required field: {f}")

    kind = (e.get("kind") or "").strip().lower()
    if kind and kind not in VALID_KINDS:
        errors.append(f"invalid kind: {kind!r}")

    cefr = (e.get("cefr") or "").strip()
    if cefr and not is_valid_cefr(cefr):
        errors.append(f"invalid cefr: {cefr!r}")
    # CEFR 必须记录来源；不允许凭空声称等级
    if cefr and not (e.get("cefr_source") or "").strip():
        errors.append("cefr set but cefr_source empty")

    # 粤语必须保留 Jyutping
    if (e.get("language_code") or "") == "yue" and not (e.get("jyutping") or "").strip():
        errors.append("yue entry missing jyutping")

    if e.get("commercial_allowed") is True and not (e.get("license") or "").strip():
        errors.append("commercial_allowed true but license empty")

    if not (e.get("meaning_en") or "").strip() and not (e.get("meaning_zh") or "").strip():
        warnings.append("no meaning_en/zh")
    if not (e.get("example_en") or "").strip() and not (e.get("example_zh") or "").strip():
        warnings.append("no example")

    return errors, warnings
