"""多语释义 / 「自动翻译」服务。

定位
----
规格 §15 要求粤语课程给出 **Chinese / Jyutping / English explanation / Example**，
而 English 版面又要求**界面上不出现中文**。两者共同需要一个：

    「给任一学习内容条目，产出它的目标语言版本」的能力。

实现方式（诚实原则，不接外部密钥）
----------------------------------
本模块使用**内置教学释义库** ``app/data/explanations.json``（全部原创手写，
不抓取、不复制商业词典），提供：

- :func:`lookup` —— 查释义
- :func:`explain_content` —— 给 ContentItem 产出完整的多语字段包
- :func:`backfill_english` —— 把老数据的 ``meaning_en`` / ``example_en`` 补齐

若未来接入机器翻译（DeepL / OpenAI 等），只需替换 :func:`_machine_translate`
的实现，其余调用方无需改动 —— 这是预留的接口位。

English 版面的兜底策略
----------------------
查不到英文释义时返回 ``""``，**绝不回退到中文**（由 :mod:`localization` 保证）。
此时模板会自动改显示「英文例句 + IPA + 词性」，依然做到零中文。
"""
from __future__ import annotations

import json
import logging
import os
import threading

from extensions import db

logger = logging.getLogger(__name__)

_DATA_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                          "app", "data", "explanations.json")
_EXPLAIN: dict = {}
_lock = threading.Lock()


# ==========================================================================
# 资源加载
# ==========================================================================
def load(force: bool = False) -> dict:
    """加载释义库（幂等，结果缓存）。"""
    global _EXPLAIN
    with _lock:
        if _EXPLAIN and not force:
            return _EXPLAIN
        try:
            with open(_DATA_PATH, encoding="utf-8") as f:
                _EXPLAIN = json.load(f)
        except FileNotFoundError:  # pragma: no cover
            _EXPLAIN = {}
            logger.warning("释义库缺失：%s", _DATA_PATH)
        return _EXPLAIN


def _key(lang_code: str, kind: str, surface: str) -> str:
    return f"{lang_code}::{kind}::{surface}"


def lookup(lang_code: str, kind: str, surface: str) -> dict:
    """查条目的多语字段。返回 {} 表示暂无数据。

    粤语条目在库里的 key 不带 kind（``yue::<surface>``），先按带 kind 查，
    再退到语言级查。
    """
    data = load()
    if not data:
        return {}
    s = (surface or "").strip()
    return (data.get(_key(lang_code, kind, s))
            or data.get(f"{lang_code}::{s}")
            or data.get(_key(lang_code, kind, s.lower()))
            or {})


# ==========================================================================
# 预留：机器翻译接口位
# ==========================================================================
def _machine_translate(text: str, src: str, dst: str) -> str:  # pragma: no cover
    """机器翻译占位实现。

    当前返回空串，表示「不做、也不伪造」——宁可让页面少一行释义，
    也不把未经校对的内容当成真翻译给用户。接入真实服务时在此实现，
    并在 :func:`explain_content` 里作为最後兜底调用即可。
    """
    return ""


# ==========================================================================
# 产出完整多语字段
# ==========================================================================
def explain_content(content_item) -> dict:
    """给一个 ContentItem 产出多语字段包（不落库，纯计算）。

    返回 dict 含 ``meaning_en`` / ``example_en`` / ``phonetic``，
    缺失字段为空串（**不会塞入中文**）。
    """
    if content_item is None:
        return {"meaning_en": "", "example_en": "", "phonetic": ""}
    item = content_item
    lang_code = _lang_of(item)
    kind = (getattr(item, "kind", "") or "").strip()
    surface = (getattr(item, "surface", "") or "").strip()

    hit = lookup(lang_code, kind, surface)
    out = {
        "meaning_en": (getattr(item, "meaning_en", "") or "").strip() or hit.get("meaning_en", ""),
        "example_en": (getattr(item, "example_en", "") or "").strip() or hit.get("example_en", ""),
        "phonetic": (getattr(item, "phonetic", "") or "").strip() or hit.get("phonetic", ""),
    }

    # 最后兜底：机器翻译预留位（当前恒为空，不改变任何行为）
    if not out["meaning_en"] and getattr(item, "meaning_cn", ""):
        out["meaning_en"] = _machine_translate(item.meaning_cn, "zh", "en")
    return out


def _lang_of(item) -> str:
    """从 ContentItem 反推它属于哪门学习语言。"""
    try:
        lesson = getattr(item, "lesson", None)
        unit = getattr(lesson, "unit", None)
        course = getattr(unit, "course", None)
        if course and course.language_code:
            return course.language_code
    except Exception:  # pragma: no cover
        pass
    return "en"


# ==========================================================================
# 回填老数据
# ==========================================================================
def backfill_english(app) -> int:
    """把已有 ContentItem 的 ``meaning_en`` / ``example_en`` / ``phonetic`` 补齐。

    幂等：已填过的条目跳过。返回实际更新条数。
    """
    from models import ContentItem
    updated = 0
    with app.app_context():
        try:
            items = ContentItem.query.all()
            for it in items:
                pack = explain_content(it)
                changed = False
                for field in ("meaning_en", "example_en", "phonetic"):
                    cur = getattr(it, field, "") or ""
                    want = pack.get(field, "") or ""
                    if not cur.strip() and want.strip():
                        setattr(it, field, want)
                        changed = True
                if changed:
                    updated += 1
            if updated:
                db.session.commit()
                logger.info("已回填 %s 条内容的英文释义", updated)
        except Exception as exc:  # pragma: no cover
            db.session.rollback()
            logger.warning("回填英文释义跳过（%s）", exc)
    return updated
