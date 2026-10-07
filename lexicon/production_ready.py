"""V5.3 生产就绪判定（production_ready）。

规则（见规格 §36 / V5.2 模型注释）：
- synthetic-dev 永远 False（开发 / 测试占位，绝不进入生产内容）。
- legacy-2000 在许可证经人工核实前永远 False（license_registry 中 verified=false）。
- 其它来源：verified(条目) AND commercial_allowed(条目，导入时已按注册表约束)
  AND required_fields_valid(必填字段有效) 三者同时成立才为 True。

commercial_allowed 必须来自 license_registry（apply_license 在导入时已强制覆盖），
绝不信任条目自身声明的 commercial_allowed；本模块对 legacy-2000 进一步查注册表的
verified 标志，做二次保险。
"""
from __future__ import annotations

from .license_filter import load_registry

# 永远不可生产的来源（硬规则）
_NEVER_PRODUCTION = {"synthetic-dev"}


def required_fields_valid(e: dict) -> bool:
    """判定条目是否拥有可进入生产内容所需的最小有效字段。"""
    lang = (e.get("language_code") or "").strip().lower()
    surface = (e.get("surface") or "").strip()
    normalized = (e.get("normalized") or "").strip()
    meaning_en = (e.get("meaning_en") or "").strip()
    meaning_zh = (e.get("meaning_zh") or "").strip()
    license_ = (e.get("license") or "").strip()
    jyutping = (e.get("jyutping") or "").strip()
    cefr = (e.get("cefr") or "").strip()
    cefr_source = (e.get("cefr_source") or "").strip()

    if not surface or not normalized:
        return False
    # 至少要有一个语言的释义
    if not meaning_en and not meaning_zh:
        return False
    # 粤语必须保留 Jyutping
    if lang == "yue" and not jyutping:
        return False
    # 生产内容必须明示许可证（即便可商用也需可溯源）
    if not license_:
        return False
    # 若声明了 CEFR，必须记录来源（不允许凭空声称等级）
    if cefr and not cefr_source:
        return False
    return True


def compute_production_ready(e: dict) -> bool:
    """依据规则计算 production_ready。

    e 为入库前的 dict（含 source / verified / commercial_allowed /
    license / 各必填字段）。返回是否可进入生产内容。
    """
    source = (e.get("source") or "").strip()

    # 硬规则：开发占位永远不可生产
    if source in _NEVER_PRODUCTION:
        return False

    # 旧 2000 词库：只有注册表明确 verified + commercial_allowed 才放行，
    # 否则（当前）一律 False。
    if source == "legacy-2000":
        reg = load_registry().get("legacy-2000", {})
        if not (reg.get("verified") and reg.get("commercial_allowed")):
            return False
        return bool(reg.get("verified")) and bool(reg.get("commercial_allowed")) \
            and required_fields_valid(e)

    # 其它来源：verified + commercial_allowed（注册表约束）+ 字段有效
    verified = bool(e.get("verified"))
    commercial = bool(e.get("commercial_allowed"))
    return verified and commercial and required_fields_valid(e)


def _entry_to_dict(row) -> dict:
    """把 ORM 行转成 compute_production_ready 需要的 dict。"""
    return {
        "source": row.source,
        "language_code": row.language_code,
        "surface": row.surface,
        "normalized": row.normalized,
        "meaning_en": row.meaning_en,
        "meaning_zh": row.meaning_zh,
        "license": row.license,
        "jyutping": row.jyutping,
        "cefr": row.cefr,
        "cefr_source": row.cefr_source,
        "verified": row.verified,
        "commercial_allowed": row.commercial_allowed,
    }


def recompute_production_ready(session, source=None, dry_run: bool = False) -> dict:
    """对已入库的 LexiconEntry 重算 production_ready。

    基于数据库对象（ORM）计算，避免依赖 dict 字段别名。
    支持按 source 过滤；dry_run 只统计不写库。
    """
    from models import LexiconEntry

    q = session.query(LexiconEntry)
    if source:
        q = q.filter(LexiconEntry.source == source)
    rows = q.all()

    changed = 0
    by_source: dict = {}
    for row in rows:
        entry_dict = _entry_to_dict(row)
        new_val = compute_production_ready(entry_dict)
        src = row.source or "(none)"
        bucket = by_source.setdefault(src, {"total": 0, "production_ready": 0, "changed": 0})
        bucket["total"] += 1
        if new_val:
            bucket["production_ready"] += 1
        if row.production_ready != new_val:
            bucket["changed"] += 1
            changed += 1
            if not dry_run:
                row.production_ready = new_val

    if not dry_run:
        session.commit()
    return {"total": len(rows), "changed": changed, "by_source": by_source, "dry_run": dry_run}
