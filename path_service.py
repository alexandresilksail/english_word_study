"""学习路径服务：词库分级回填 + 用户路径进度计算。"""
from __future__ import annotations

import logging

from sqlalchemy.exc import IntegrityError

from extensions import db
from learning_path import (LEVELS, LEVEL_BY_CODE, UNITS_PER_LEVEL, UNLOCK_RATIO,
                           assign_by_quantile, cefr_from_freq)
from models import UserWordProgress, Word, WordMeta

logger = logging.getLogger(__name__)


def _missing_meta_rows() -> list[WordMeta]:
    """计算「还没有分级记录」的词，并生成对应的 WordMeta 行（只算不写）。

    与 seed 顺序无关：create_app 先跑、词库后 seed 也没关系 ——
    下次启动（或测试里再次调用）会把缺失词补齐。words 表一行不动。
    """
    existing = {m.word_id for m in WordMeta.query.all()}
    # NOTE: Word.freq 是词频**排名**（越小越常用："be"=2、"in"=6；
    # 99999 为未知/极低频的哨兵值）。因此按 ASC 排 —— 升序才是「由常用到生僻」。
    words = [w for w in Word.query.order_by(Word.freq.asc(), Word.id.asc()).all()
             if w.id not in existing]
    if not words:
        return []

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
    return rows


def ensure_word_meta() -> int:
    """幂等回填 word_meta：只为还没有分级记录的词补分级。words 表一行不动。

    并发安全（根因修复）：
    Gunicorn 多 worker / 多容器会对**同一个 SQLite 文件**同时执行本函数。
    「读现有集合 → 算缺失 → 插入」不是原子的：两个进程可能算出同一批缺失词，
    后提交者会撞 ``word_meta.word_id`` 唯一约束（如 word_id=110）。
    这里在 IntegrityError 时**回滚并重读补集**，把“插入缺口”收敛为 0——
    这是对幂等逻辑的重新执行（第二次重读时另一进程已提交的行会进入 existing），
    不是跳过约束或吞掉异常；重试耗尽后原样抛出，交由调用方回滚。
    """
    last_exc: IntegrityError | None = None
    for attempt in range(3):
        rows = _missing_meta_rows()
        if not rows:
            return 0
        try:
            db.session.add_all(rows)
            db.session.commit()
            logger.info("word_meta 补齐 %d 词（累计 %d）",
                        len(rows), WordMeta.query.count())
            return len(rows)
        except IntegrityError as exc:
            last_exc = exc
            db.session.rollback()
            if attempt + 1 >= 3:
                break
            logger.warning("word_meta 检测到并发写入冲突（第 %d 次），回滚后重试",
                           attempt + 1)
    if last_exc is not None:
        raise last_exc
    return 0


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


# --------------------------------------------------------------------------
# V6.0.3 个性化学习路径：基于 LearnerProfile（评估得出的等级 + 弱项）生成下一步
# --------------------------------------------------------------------------
def personalized_plan(user_id: int) -> dict:
    """返回用户专属的下一步学习序列。

    - 无画像：引导先做能力评估（Assessment → Learner Model 闭环入口）。
    - 有画像：弱项专项优先 → 当前 CEFR 等级主题单元 → 间隔复习。
    纯函数式推导，不落新表；画像本身已持久化在 learner_profiles。
    """
    from flask import url_for
    from models import LearnerProfile, SKILL_LABELS_ZH

    prof = LearnerProfile.query.get(user_id)
    if not prof:
        return {
            "has_profile": False,
            "overall_level": "A1",
            "weak_areas": [],
            "steps": [{
                "type": "assess", "priority": 0,
                "title": "先做一次能力评估",
                "description": "完成 AI 能力评估，系统会据此生成专属学习路径与弱项训练。",
                "href": url_for("assessment.index"),
            }],
        }

    overall = prof.overall_level or "A1"
    weak = [a for a in (prof.weak_areas or "").split(",") if a]

    steps = []
    for skill in weak:
        label = SKILL_LABELS_ZH.get(skill, skill)
        steps.append({
            "type": "focus", "skill": skill, "priority": 1,
            "title": f"强化弱项：{label}",
            "description": f"当前 {label} 等级 {prof.level_of(skill)}，建议专项训练以拉平短板。",
            "href": url_for("practice.index") + f"?skill={skill}",
        })

    from learning_path import LEVEL_BY_CODE, UNIT_THEMES
    lv = LEVEL_BY_CODE.get(overall, LEVEL_BY_CODE["A1"])
    for i, (zh, en) in enumerate(UNIT_THEMES.get(lv.code, []), start=1):
        steps.append({
            "type": "unit", "level": lv.code, "priority": 2,
            "title": f"{lv.code} · {zh} ({en})",
            "description": f"按 CEFR {lv.code} 主题有序推进学习。",
            "href": url_for("learn.hub") + f"?cefr={lv.code}&unit={i}",
        })

    steps.append({
        "type": "review", "priority": 3,
        "title": "间隔复习（抗遗忘）",
        "description": "按遗忘曲线与弱项优先级安排复习。",
        "href": url_for("main.review"),
    })

    steps.sort(key=lambda s: s["priority"])
    return {
        "has_profile": True,
        "overall_level": overall,
        "weak_areas": weak,
        "steps": steps,
    }

