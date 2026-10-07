"""学习路径服务：词库分级回填 + 用户路径进度计算。"""
from __future__ import annotations

import logging

from extensions import db
from learning_path import (LEVELS, LEVEL_BY_CODE, UNITS_PER_LEVEL, UNLOCK_RATIO,
                           assign_by_quantile, cefr_from_freq)
from models import UserWordProgress, Word, WordMeta

logger = logging.getLogger(__name__)


def ensure_word_meta() -> int:
    """幂等回填 word_meta：只为还没有分级记录的词补分级。

    与 seed 顺序无关：create_app 先跑、词库后 seed 也没关系 ——
    下次启动（或测试里再次调用）会把缺失词补齐。words 表一行不动。
    """
    existing = {m.word_id for m in WordMeta.query.all()}
    # NOTE: Word.freq 是词频**排名**（越小越常用："be"=2、"in"=6；
    # 99999 为未知/极低频的哨兵值）。因此按 ASC 排 —— 升序才是「由常用到生僻」。
    words = [w for w in Word.query.order_by(Word.freq.asc(), Word.id.asc()).all()
             if w.id not in existing]
    if not words:
        return 0

    # ── 分级：优先按「已入库词的词频分位」整体重算，保证分布合理 ──────────
    # 只有在增量补齐（库里已有大量分级）时，才单独给新词按分位定档。
    total_words = Word.query.count()
    assigned: dict[int, str] = {}
    if total_words == len(words):
        # 首次全量回填：对整份词库做分位切片
        all_words = Word.query.order_by(Word.freq.asc(), Word.id.asc()).all()
        assigned = assign_by_quantile(all_words)
    else:
        assigned = assign_by_quantile(words)

    # ── 单元：同等级内按词频降序均分到 UNITS_PER_LEVEL 个单元 ────────────
    buckets: dict[str, list[Word]] = {}
    for w in words:
        buckets.setdefault(assigned.get(w.id, LEVELS[-1].code), []).append(w)

    rows = []
    for code, bucket in buckets.items():
        lv = LEVEL_BY_CODE.get(code, LEVELS[-1])
        n = len(bucket)
        for i, w in enumerate(bucket):
            unit = min(UNITS_PER_LEVEL, (i * UNITS_PER_LEVEL // max(n, 1)) + 1)
            rows.append(WordMeta(
                word_id=w.id,
                learning_language="en",
                cefr_level=code,
                unit_no=unit,
                difficulty=lv.order + 1,
                category="general",
            ))
    db.session.add_all(rows)
    db.session.commit()
    logger.info("word_meta 补齐 %d 词（累计 %d）", len(rows), WordMeta.query.count())
    return len(rows)


def level_progress(uid: int) -> list[dict]:
    """返回 6 个等级的进度（供 /path 页与 Dashboard 路径条共用）。"""
    out = []
    prev_ratio = 1.0  # A1 总是解锁
    for lv in LEVELS:
        metas = WordMeta.query.filter_by(cefr_level=lv.code).all()
        total = len(metas)
        word_ids = [m.word_id for m in metas]
        mastered = (UserWordProgress.query
                    .filter(UserWordProgress.user_id == uid,
                             UserWordProgress.word_id.in_(word_ids),
                             UserWordProgress.status == "mastered")
                    .count()) if word_ids else 0
        ratio = (mastered / total) if total else 0.0
        unlocked = prev_ratio >= UNLOCK_RATIO or lv.order == 0
        done = ratio >= UNLOCK_RATIO
        out.append({
            "code": lv.code, "order": lv.order,
            "name_zh": lv.name_zh, "name_en": lv.name_en,
            "color": lv.color, "emoji": lv.emoji,
            "total": total, "mastered": mastered,
            "ratio": ratio, "unlocked": unlocked, "done": done,
            "units": UNITS_PER_LEVEL,
        })
        prev_ratio = ratio
    # 当前节点：第一个 unlocked 且未 done 的等级
    current = next((l for l in out if l["unlocked"] and not l["done"]), None)
    return out, current


def learn_filters(cefr: str = "", unit: str = "") -> dict:
    """/learn 页按 CEFR 等级 / 单元过滤时返回 word_id 集合（空=不过滤）。"""
    q = WordMeta.query
    if cefr:
        q = q.filter_by(cefr_level=cefr)
    if unit:
        q = q.filter_by(unit_no=int(unit))
    ids = [m.word_id for m in q.all()]
    return {"word_ids": ids}
