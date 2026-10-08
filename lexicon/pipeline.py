"""处理流水线：Raw -> Normalize -> License Filter -> Dedup -> Validate -> Master。"""
from __future__ import annotations

from .normalize import normalize
from .dedup import dedup_entries
from .license_filter import apply_license
from .validate import validate_entry
from .cefr import compute_difficulty
from .production_ready import compute_production_ready


def build_entry(raw: dict) -> dict:
    e = dict(raw)
    e["language_code"] = (e.get("language_code") or "").strip().lower()
    e["surface"] = (e.get("surface") or "").strip()
    e["normalized"] = normalize(e["language_code"], e.get("normalized") or e["surface"])
    e["kind"] = (e.get("kind") or "vocabulary").strip().lower()
    e["pos"] = (e.get("pos") or "").strip().lower()
    if not (e.get("lemma") or "").strip():
        e["lemma"] = e["surface"]
    e["topic"] = (e.get("topic") or "general").strip()
    e["difficulty"] = compute_difficulty(e.get("cefr", ""), e.get("frequency_rank"))
    e = apply_license(e)
    # V5.3：依据规则（source / verified / commercial_allowed / 必填字段）计算生产就绪状态，
    # 作为入库前的权威值；导入脚本也可显式覆盖。
    e["production_ready"] = compute_production_ready(e)
    return e


def run_pipeline(raw_iterable, strict=True):
    """返回 (master_entries, stats)。

    strict=True 时遇到校验硬错误立即抛出；strict=False 时跳过错误条目。
    """
    built, stats = [], {"total": 0, "dedup_removed": 0, "errors": 0, "warnings": 0, "kept": 0}
    for raw in raw_iterable:
        stats["total"] += 1
        e = build_entry(raw)
        errs, warns = validate_entry(e)
        stats["warnings"] += len(warns)
        if errs:
            stats["errors"] += 1
            if strict:
                raise ValueError(
                    f"validation error on {e.get('source_id') or e.get('surface')}: {errs}"
                )
            continue
        built.append(e)
    before = len(built)
    built = dedup_entries(built)
    stats["dedup_removed"] = before - len(built)
    stats["kept"] = len(built)
    return built, stats
