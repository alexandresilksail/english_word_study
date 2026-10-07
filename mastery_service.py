"""V5.6 统一掌握度（Mastery 0-4）+ 间隔重复写入口。

设计要点
--------
* 与 ReviewItem（Leitner 抗遗忘队列）解耦：ReviewItem 管「何时复习」，
  ContentMastery 管「掌握到什么程度」。
* **复习时间的单一真源**：``next_review_at`` 由 ReviewItem 的 Leitner 盒子决定，
  :func:`record_practice` 只做搬运，保证两张表的到期时间永远一致。
  （V5.5 曾分别在两处各算一次间隔，随时间推移会悄悄分叉——已修正。）
* :func:`record_practice` 是学习结果的唯一写入口：
  既更新 ReviewItem，也更新 ContentMastery 的计数 / streak / 弱项与 level。

掌握度 0-4 是**综合信号**，见 :func:`level_from_record` 的文档。

所有涉及用户数据的查询都带 user_id 过滤，确保用户数据隔离。
"""
from __future__ import annotations

import json
import logging
from datetime import timedelta

from extensions import db
from models import ContentItem, ContentMastery, ReviewItem, utcnow
from review_service import record_result

logger = logging.getLogger(__name__)

#: Leitner 间隔（与 review_service 保持一致），仅作无 ReviewItem 时的兜底
_INTERVALS_DAYS = [1, 3, 7, 14, 30]

#: 统一掌握度标签（UI 双语）
LEVEL_LABELS = {
    0: {"en": "New", "zh": "未学"},
    1: {"en": "Learning", "zh": "学习中"},
    2: {"en": "Familiar", "zh": "熟悉"},
    3: {"en": "Strong", "zh": "扎实"},
    4: {"en": "Mastered", "zh": "已掌握"},
}


def level_label(level: int, lang: str = "zh") -> str:
    labels = LEVEL_LABELS.get(int(level or 0), LEVEL_LABELS[0])
    return labels.get("en" if lang == "en" else "zh", labels["zh"])


def get_or_create(user_id: int, content_id: int) -> ContentMastery:
    row = ContentMastery.query.filter_by(user_id=user_id, content_id=content_id).first()
    if row:
        return row
    row = ContentMastery(
        user_id=user_id, content_id=content_id,
        first_learned_at=utcnow(), last_seen_at=utcnow(),
        next_review_at=utcnow() + timedelta(days=1),
    )
    db.session.add(row)
    db.session.flush()
    return row


def level_from_record(m: ContentMastery) -> int:
    """V5.6 统一掌握度 0-4（替代 V5.5 的初步规则）。

    三类证据合并判定：

    * **A 练习证据** —— correct/wrong（正确率 acc）、streak（连对）、
      review_count（累计练习次数）
    * **B 课时小测** —— ``quiz_score``（0-100）
    * **C 单元测试** —— ``unit_test_score``（0-100，权威性最高）

    .. note:: 关于 0 的语义

       ``quiz_score`` / ``unit_test_score`` 用 **0 表示「未测验」**（保持既有默认
       值语义）。因此这两个分数**只作为正向证据**抬高掌握度，绝不因为低分倒扣——
       否则「没测过」会被误判成「测得很差」。

       负向证据一律走 ``wrong_count`` / ``weak`` 字段，由
       :func:`record_practice` 在 ``correct=False`` 时维护。

    判定顺序（自上而下，命中即返回）：

    =====  ========  ==========================================================
    level  标签      条件
    =====  ========  ==========================================================
    4      Mastered  练习又稳又久（acc>=90 且 streak>=5 且 rev>=5）**且**
                     有权威测验佐证（>=85）；或单元测试接近满分（>=95 且 rev>=3）
    3      Strong    练习较好（acc>=80 且 streak>=3 且 rev>=3）；
                     或测验佐证 >=75 且练过 >=2 次；或单元测试 >=75
    2      Familiar  acc>=60 且 streak>=2 且 rev>=2；或测验佐证 >=60；
                     或练过 >=3 次且 acc>=60
    1      Learning  有任何学习记录（练习过 / 测过）
    0      New       完全无记录
    =====  ========  ==========================================================
    """
    m = m or ContentMastery()

    correct = int(m.correct_count or 0)
    wrong = int(m.wrong_count or 0)
    total = correct + wrong
    review_count = int(m.review_count or 0)
    streak = int(m.streak or 0)
    quiz = int(m.quiz_score or 0)
    unit = int(m.unit_test_score or 0)
    tested = max(quiz, unit)  # 0 = 从没测验过

    if not (total or review_count or quiz or unit):
        return 0  # 完全没学过

    acc = round(correct * 100 / total) if total else 0

    # ---- 4 Mastered ---------------------------------------------------
    if acc >= 90 and streak >= 5 and review_count >= 5 and tested >= 85:
        return 4
    if unit >= 95 and review_count >= 3:
        return 4

    # ---- 3 Strong -----------------------------------------------------
    if acc >= 80 and streak >= 3 and review_count >= 3:
        return 3
    if tested >= 75 and review_count >= 2:
        return 3
    if unit >= 75:
        return 3

    # ---- 2 Familiar ---------------------------------------------------
    if acc >= 60 and streak >= 2 and review_count >= 2:
        return 2
    if tested >= 60:
        return 2
    if review_count >= 3 and acc >= 60:
        return 2

    return 1


def _add_weak_reason(m: ContentMastery, kind: str, given=None, expected=None) -> None:
    try:
        reasons = json.loads(m.weak_reasons or "[]") or []
    except Exception:
        reasons = []
    if not isinstance(reasons, list):
        reasons = []
    # 同 kind 只保留一条最新记录，避免 reasons 无限膨胀
    reasons = [r for r in reasons if isinstance(r, dict) and r.get("kind") != kind][:4]
    reasons.insert(0, {
        "kind": kind or "practice",
        "given": (given or "")[:120],
        "expected": (expected or "")[:120],
    })
    m.weak_reasons = json.dumps(reasons[:5], ensure_ascii=False)


def record_practice(user_id: int, content_id: int, correct: bool,
                    kind: str | None = None, mode: str | None = None,
                    given: str | None = None, expected: str | None = None) -> ContentMastery:
    """记录一次学习结果（供 /practice/submit、单元测验等所有写路径调用）。

    1. 更新 ReviewItem（Leitner 抗遗忘队列，复用既有逻辑）；
    2. 更新 ContentMastery 计数 / streak / 弱项，并按统一规则算出 level；
    3. ``next_review_at`` **直接取自 ReviewItem** —— 复习时刻只有 Leitner
       一个真源，避免两张表各自算间隔而缓慢分叉。
    """
    content_id = int(content_id)
    # 1) Leitner 队列（既有逻辑，内部自行 commit）
    review_item = record_result(user_id, content_id, bool(correct))

    # 2) Mastery 计数
    m = get_or_create(user_id, content_id)
    if m.first_learned_at is None:
        m.first_learned_at = utcnow()
    m.last_seen_at = utcnow()
    m.review_count = (m.review_count or 0) + 1

    if correct:
        m.correct_count = (m.correct_count or 0) + 1
        m.streak = (m.streak or 0) + 1
        # 答对即清除弱项标记（该原因已消除）
        if m.weak:
            m.weak = False
            m.weak_reasons = "[]"
    else:
        m.wrong_count = (m.wrong_count or 0) + 1
        m.streak = 0
        m.weak = True
        _add_weak_reason(m, kind or mode or "practice", given=given, expected=expected)

    m.level = level_from_record(m)

    # 3) 复习时刻：单一真源 = ReviewItem.next_review_at
    if review_item is not None and review_item.next_review_at is not None:
        m.next_review_at = review_item.next_review_at
    else:  # 兜底（正常情况下不会发生）
        box = min(max(m.level, 1), len(_INTERVALS_DAYS))
        days = 1 if not correct else _INTERVALS_DAYS[box - 1]
        m.next_review_at = utcnow() + timedelta(days=days)

    m.updated_at = utcnow()
    db.session.commit()
    return m


def record_quiz_score(user_id: int, content_id: int, score_pct: int) -> ContentMastery:
    """记录一次针对该内容的课时小测得分（百分比），作为掌握度正向证据。"""
    m = get_or_create(user_id, int(content_id))
    m.quiz_score = max(0, min(100, int(score_pct or 0)))
    m.last_seen_at = utcnow()
    m.level = level_from_record(m)
    m.updated_at = utcnow()
    db.session.commit()
    return m


def record_unit_test_score(user_id: int, content_id: int, score_pct: int) -> ContentMastery:
    """记录单元测试得分（百分比），这是权威性最高的掌握度证据。"""
    m = get_or_create(user_id, int(content_id))
    m.unit_test_score = max(0, min(100, int(score_pct or 0)))
    m.last_seen_at = utcnow()
    m.level = level_from_record(m)
    m.updated_at = utcnow()
    db.session.commit()
    return m


def record_unit_test_results(user_id: int, results: list[dict],
                             accuracy: int | None = None) -> int:
    """V5.6：单元测验作答结果批量入账。

    ``results`` 每项形如 ``{content_id, correct, kind, given, expected}``：

    * 每个被考到的内容，按 ``correct`` 走 :func:`record_practice`
      （``mode="unit_test"``）—— 单元测试是一次真实曝光，同样进 Leitner 队列；
    * 整份测验的 ``accuracy`` 写入 ``unit_test_score``，作为该条目的权威证据。

    返回实际更新的条目数。
    """
    n = 0
    for r in results or []:
        try:
            cid = int(r.get("content_id"))
        except (KeyError, TypeError, ValueError):
            continue
        correct = bool(r.get("correct"))
        m = record_practice(user_id, cid, correct,
                            kind=r.get("kind") or "quiz", mode="unit_test",
                            given=r.get("given"), expected=r.get("expected"))
        if accuracy is not None:
            m.unit_test_score = max(0, min(100, int(accuracy)))
            m.level = level_from_record(m)
        n += 1
    try:
        db.session.commit()
    except Exception as exc:  # pragma: no cover - 不影响判分主链路
        db.session.rollback()
        logger.warning("单元测试掌握度入账失败（%s）", exc)
    return n


def recompute(user_id: int | None = None) -> int:
    """按当前统一 0-4 规则重算 level，返回变更行数。

    用途：V5.5 初步规则算出的老行需要升级到 V5.6 规则；规则再次演进时同理。
    幂等，可随时执行。
    """
    q = ContentMastery.query
    if user_id is not None:
        q = q.filter(ContentMastery.user_id == user_id)
    changed = 0
    for m in q.all():
        new_level = level_from_record(m)
        if new_level != m.level:
            m.level = new_level
            changed += 1
    if changed:
        db.session.commit()
    return changed


def distribution(user_id: int) -> dict:
    """用户掌握度分布（按 level 0-4 计数），供 Dashboard 展示。"""
    rows = (db.session.query(ContentMastery.level, db.func.count())
            .filter(ContentMastery.user_id == user_id)
            .group_by(ContentMastery.level).all())
    dist = {0: 0, 1: 0, 2: 0, 3: 0, 4: 0}
    for level, n in rows:
        dist[int(level)] = n
    return dist


def weak_areas(user_id: int, limit: int = 10) -> list[dict]:
    """用户的弱项清单（供 Dashboard Weak Areas）。"""
    rows = (ContentMastery.query
            .filter(ContentMastery.user_id == user_id, ContentMastery.weak.is_(True))
            .order_by(ContentMastery.updated_at.desc())
            .limit(limit).all())
    out = []
    for m in rows:
        item = db.session.get(ContentItem, m.content_id)
        try:
            reasons = json.loads(m.weak_reasons or "[]") or []
        except Exception:
            reasons = []
        out.append({
            "content_id": m.content_id,
            "surface": item.surface if item else "",
            "kind": item.kind if item else "",
            "level": m.level,
            "level_label": level_label(m.level),
            "wrong_count": m.wrong_count,
            "reasons": reasons,
        })
    return out


def review_today(user_id: int, limit: int = 20) -> list[dict]:
    """V5.6 Review Today：``next_review_at <= now`` 的到期项。

    同时带上掌握度上下文，便于在复习页显示「这条上次错在哪」。
    """
    now = utcnow()
    rows = (db.session.query(ReviewItem, ContentItem, ContentMastery)
            .join(ContentItem, ContentItem.id == ReviewItem.content_id)
            .outerjoin(ContentMastery, db.and_(
                ContentMastery.content_id == ReviewItem.content_id,
                ContentMastery.user_id == ReviewItem.user_id))
            .filter(ReviewItem.user_id == user_id, ReviewItem.next_review_at <= now)
            .order_by(ReviewItem.next_review_at.asc())
            .limit(limit).all())
    out = []
    for rev, content, mastery in rows:
        level = mastery.level if mastery is not None else 0
        out.append({
            "review_id": rev.id,
            "content_id": content.id,
            "surface": content.surface,
            "kind": content.kind,
            "box": rev.box,
            "due_at": rev.next_review_at,
            "level": level,
            "level_label": level_label(level),
            "correct_count": mastery.correct_count if mastery else 0,
            "wrong_count": mastery.wrong_count if mastery else 0,
        })
    return out


def review_today_count(user_id: int) -> int:
    """今日到期复习总量（Dashboard 用）。"""
    now = utcnow()
    return (ReviewItem.query
            .filter(ReviewItem.user_id == user_id, ReviewItem.next_review_at <= now)
            .count())


def mastery_overview(user_id: int) -> dict:
    """Dashboard 用的一屏汇总：掌握度分布 / 弱项 / 今日复习。"""
    dist = distribution(user_id)
    total_items = ContentItem.query.count()
    tracked = sum(n for lv, n in dist.items() if lv >= 1)
    return {
        "distribution": dist,
        "tracked": tracked,
        "new_count": dist.get(0, 0),
        "mastered": dist.get(4, 0),
        "coverage_pct": round(tracked * 100 / total_items) if total_items else 0,
        "weak_areas": weak_areas(user_id, limit=10),
        "due_today": review_today_count(user_id),
    }