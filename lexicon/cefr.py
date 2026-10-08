"""CEFR 等级与难度（确定性规则，不引入机器学习）。"""
from __future__ import annotations

CEFR_ORDER = ["Pre-A1", "A1", "A2", "B1", "B2", "C1", "C2"]
CEFR_RANK = {c: i + 1 for i, c in enumerate(CEFR_ORDER)}  # Pre-A1=1 .. C2=7


def cefr_rank(cefr: str) -> int:
    return CEFR_RANK.get((cefr or "").strip(), 0)


def is_valid_cefr(cefr: str) -> bool:
    return (cefr or "").strip() in CEFR_RANK


def compute_difficulty(cefr: str, frequency_rank=None) -> int:
    """难度映射到 1..7。

    基础取 CEFR 等级；高频词（frequency_rank 小）略微降低难度，低频词略升。
    无 CEFR 时默认中等（3），绝不凭空声称等级。
    """
    base = cefr_rank(cefr) or 3
    diff = base
    if frequency_rank is not None and frequency_rank > 0:
        if frequency_rank <= 1000:
            diff = max(1, base - 1)
        elif frequency_rank <= 5000:
            diff = base
        else:
            diff = min(7, base + 1)
    return max(1, min(7, diff))
