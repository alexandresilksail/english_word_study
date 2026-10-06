"""抗遗忘复习服务：Leitner 间隔重复（1 / 3 / 7 / 14 / 30 天）。

答对 → 升格（间隔拉长）；答错 → 降回第 1 格（间隔缩短）。
"""
from __future__ import annotations

from datetime import timedelta

from extensions import db
from models import ContentItem, ReviewItem, utcnow

INTERVALS_DAYS = [1, 3, 7, 14, 30]


def ensure_review(user_id: int, content_id: int) -> ReviewItem:
    item = ReviewItem.query.filter_by(user_id=user_id, content_id=content_id).first()
    if item:
        return item
    item = ReviewItem(user_id=user_id, content_id=content_id,
                      box=1, next_review_at=utcnow() + timedelta(days=INTERVALS_DAYS[0]))
    db.session.add(item)
    db.session.commit()
    return item


def due_items(uid: int, limit: int = 20):
    now = utcnow()
    return (ReviewItem.query.filter(ReviewItem.user_id == uid,
                                    ReviewItem.next_review_at <= now)
            .order_by(ReviewItem.next_review_at.asc())
            .join(ContentItem, ContentItem.id == ReviewItem.content_id)
            .add_entity(ContentItem)
            .limit(limit).all())


def due_count_by_kind(uid: int) -> dict:
    """Dashboard Today's Review 汇总：{vocabulary: n, phrase: n, sentence: n, grammar: n}"""
    now = utcnow()
    rows = (db.session.query(ContentItem.kind, db.func.count())
            .join(ReviewItem, ReviewItem.content_id == ContentItem.id)
            .filter(ReviewItem.user_id == uid, ReviewItem.next_review_at <= now)
            .group_by(ContentItem.kind).all())
    out = {"vocabulary": 0, "phrase": 0, "sentence": 0, "grammar": 0}
    for kind, n in rows:
        if kind in out:
            out[kind] = n
    return out


def record_result(uid: int, content_id: int, correct: bool) -> ReviewItem:
    item = ReviewItem.query.filter_by(user_id=uid, content_id=content_id).first()
    if not item:
        item = ensure_review(uid, content_id)
    now = utcnow()
    if correct:
        item.box = min(item.box + 1, len(INTERVALS_DAYS))
        item.correct_streak += 1
    else:
        item.box = 1
        item.wrong_count += 1
        item.correct_streak = 0
    interval = INTERVALS_DAYS[item.box - 1]
    item.next_review_at = now + timedelta(days=interval)
    item.last_reviewed_at = now
    item.review_count += 1
    db.session.commit()
    return item
