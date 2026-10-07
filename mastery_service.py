"""V5.5 Mastery 写路径 + 初步 level 计算。

设计要点
--------
* 与 ReviewItem（Leitner 抗遗忘队列）解耦：ReviewItem 管「何时复习」，
  ContentMastery 管「掌握到什么程度」。
* :func:`record_practice` 是 /practice/submit 的唯一写入口：
  它既更新 ReviewItem（复用既有 Leitner 逻辑），也更新 ContentMastery 的
  计数 / streak / 弱项，并算出初步 level。
* 本文件的 ``level_from_record`` 给出 **V5.5 初步规则**；V5.6 会在同一函数里
  纳入 quiz / unit test 分数，升级为「统一 Mastery 0-4」计算，调用方无需改动。

所有涉及用户数据的查询都带 user_id 过滤，确保用户数据隔离。
"""
from __future__ import annotations

import json
from datetime import timedelta

from extensions import db
from models import ContentItem, ContentMastery, utcnow
from review_service import record_result

#: Leitner 间隔（与 review_service 保持一致），用于推算 next_review_at
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
    """V5.5 初步规则（V5.6 将扩展为纳入 quiz / unit test 的统一 0-4）。

    0 New · 1 Learning · 2 Familiar · 3 Strong · 4 Mastered
    纯按「正确率 + 连对 + 复习次数」粗分；V5.6 叠加测验/单元测试得分后会更稳。
    """
    m = m or ContentMastery()
    total = (m.correct_count or 0) + (m.wrong_count or 0)
    if total == 0:
        return 0
    acc = (m.correct_count or 0) * 100 // total if total else 0
    streak = m.streak or 0
    rev = m.review_count or 0
    if acc >= 90 and streak >= 5 and rev >= 5:
        return 4
    if acc >= 80 and streak >= 3 and rev >= 3:
        return 3
    if acc >= 60 and streak >= 2 and rev >= 2:
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
    """记录一次练习结果（供 /practice/submit 调用）。

    1. 更新 ReviewItem（Leitner 抗遗忘队列，复用既有逻辑）；
    2. 更新 ContentMastery 计数 / streak / 弱项，并算出初步 level；
    3. 计算 next_review_at（答对拉长、答错缩短）。
    """
    content_id = int(content_id)
    # 1) Leitner 队列（既有逻辑，内部自行 commit）
    record_result(user_id, content_id, bool(correct))

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
        _add_weak_reason(m, kind or "practice", given=given, expected=expected)

    m.level = level_from_record(m)

    # 3) next_review_at：基于 level 与连对推算间隔（与 Leitner 同向）
    box = min(max(m.level, 1), len(_INTERVALS_DAYS))
    interval = _INTERVALS_DAYS[box - 1]
    if not correct:
        interval = 1
    m.next_review_at = utcnow() + timedelta(days=interval)
    m.updated_at = utcnow()

    db.session.commit()
    return m


def record_quiz_score(user_id: int, content_id: int, score_pct: int) -> ContentMastery:
    """记录一次针对该内容的测验得分（百分比），V5.6 统一 level 会用到。"""
    m = get_or_create(user_id, int(content_id))
    m.quiz_score = max(0, min(100, int(score_pct or 0)))
    m.last_seen_at = utcnow()
    m.level = level_from_record(m)
    m.updated_at = utcnow()
    db.session.commit()
    return m


def record_unit_test_score(user_id: int, content_id: int, score_pct: int) -> ContentMastery:
    """记录单元测试得分（百分比），V5.6 统一 level 会用到。"""
    m = get_or_create(user_id, int(content_id))
    m.unit_test_score = max(0, min(100, int(score_pct or 0)))
    m.last_seen_at = utcnow()
    m.level = level_from_record(m)
    m.updated_at = utcnow()
    db.session.commit()
    return m


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
        item = ContentItem.query.get(m.content_id)
        try:
            reasons = json.loads(m.weak_reasons or "[]") or []
        except Exception:
            reasons = []
        out.append({
            "content_id": m.content_id,
            "surface": item.surface if item else "",
            "kind": item.kind if item else "",
            "level": m.level,
            "wrong_count": m.wrong_count,
            "reasons": reasons,
        })
    return out
