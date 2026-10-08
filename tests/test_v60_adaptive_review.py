"""V6.0.4 自适应复习：在到期集合内把用户弱项相关内容排到最前。"""
from __future__ import annotations

from datetime import timedelta

import pytest

from extensions import db
from models import (ContentItem, Course, Lesson, LearnerProfile, ReviewItem,
                    Unit, User, utcnow)


def _user(app, email):
    with app.app_context():
        u = User.query.filter_by(email=email).first()
        if not u:
            u = User(email=email, username=email.split("@")[0], is_admin=False)
            u.set_password("V60testPass1!")
            db.session.add(u)
            db.session.commit()
        c = app.test_client()
        r = c.post("/login", data={"email": email, "password": "V60testPass1!"})
        assert r.status_code in (302, 303)
        return c, u.id


def _make_due_chain(app, uid, weak=("grammar",)):
    """造一条 Course→Unit→Lesson→ContentItem→ReviewItem 链，两个内容都到期。

    Course/Unit/Lesson 用 get-or-create，避免多个用例在同一会话库里撞唯一约束。
    """
    with app.app_context():
        db.session.add(LearnerProfile(
            user_id=uid, overall_level="A2", weak_areas=",".join(weak),
            vocab_level="A2", grammar_level="A2", reading_level="A2",
            listening_level="A2", speaking_level="A2", writing_level="A2"))
        course = Course.query.filter_by(language_code="xx", cefr_level="A2").first()
        if course is None:
            course = Course(language_code="xx", cefr_level="A2",
                           title_zh="测A2", title_en="Test A2")
            db.session.add(course)
            db.session.flush()
        unit = Unit.query.filter_by(course_id=course.id, no=1).first()
        if unit is None:
            unit = Unit(course_id=course.id, no=1, title_zh="U1", title_en="U1")
            db.session.add(unit)
            db.session.flush()
        lesson = Lesson.query.filter_by(unit_id=unit.id, no=1).first()
        if lesson is None:
            lesson = Lesson(unit_id=unit.id, no=1, kind="grammar",
                           title_zh="L1", title_en="L1")
            db.session.add(lesson)
            db.session.flush()
        g = ContentItem(lesson_id=lesson.id, kind="grammar", surface="grammar item")
        v = ContentItem(lesson_id=lesson.id, kind="vocabulary", surface="vocab item")
        db.session.add_all([g, v])
        db.session.flush()
        due = utcnow() - timedelta(days=2)
        db.session.add(ReviewItem(user_id=uid, content_id=g.id, box=1,
                                  next_review_at=due))
        db.session.add(ReviewItem(user_id=uid, content_id=v.id, box=1,
                                  next_review_at=due))
        db.session.commit()
        return g.id, v.id


def test_adaptive_prioritizes_weak_kind(client, app):
    c, uid = _user(app, "v60ar1@example.com")
    _make_due_chain(app, uid, weak=("grammar",))
    from review_service import adaptive_due_items
    with app.app_context():
        rows = adaptive_due_items(uid, limit=10)
        assert rows, "应当返回到期项"
        assert rows[0].ContentItem.kind == "grammar", rows[0].ContentItem.kind


def test_review_page_shows_adaptive_badge(client, app):
    c, uid = _user(app, "v60ar2@example.com")
    _make_due_chain(app, uid, weak=("grammar",))
    r = c.get("/review")
    assert r.status_code == 200
    body = r.get_data(as_text=True)
    assert "自适应" in body or "Adaptive" in body
