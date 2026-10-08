"""V6.0.3 个性化学习路径：基于 LearnerProfile（评估画像）生成下一步。"""
from __future__ import annotations

import pytest

from extensions import db
from models import User, LearnerProfile


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


def test_path_without_profile_prompts_assessment(client, app):
    """无画像：引导先做能力评估（闭环入口）。"""
    c, uid = _user(app, "v60lp_none@example.com")
    r = c.get("/path")
    assert r.status_code == 200
    body = r.get_data(as_text=True)
    assert "先做一次能力评估" in body


def test_path_with_profile_shows_weak_focus(client, app):
    """有画像且含弱项：优先展示弱项专项步骤 + 当前等级单元。"""
    c, uid = _user(app, "v60lp_weak@example.com")
    with app.app_context():
        db.session.add(LearnerProfile(
            user_id=uid, overall_level="B1", weak_areas="speaking,writing",
            vocab_level="B1", grammar_level="B1", reading_level="B1",
            listening_level="B1", speaking_level="A2", writing_level="A2"))
        db.session.commit()
    r = c.get("/path")
    assert r.status_code == 200
    body = r.get_data(as_text=True)
    assert "强化弱项" in body          # 弱项专项步骤
    assert "B1" in body               # 当前等级单元
