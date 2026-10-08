"""V6.0.1 AI Assessment + V6.0.2 Learner Model 测试。

覆盖：生成评估 → 提交判分 → 持久化 LearnerProfile / Assessment → 结果页 → IDOR 防护。
"""
from __future__ import annotations

import json

from extensions import db
from models import Assessment, LearnerProfile, User

PW = "V60testPass1!"


def _make_user(app, email, admin=False):
    with app.app_context():
        u = User(email=email, username=email.split("@")[0], is_admin=admin)
        u.set_password(PW)
        db.session.add(u)
        db.session.commit()
        return u.id


def _login(client, email):
    r = client.post("/login", data={"email": email, "password": PW})
    assert r.status_code in (302, 303)
    return client


def _gen(app, uid):
    from assessment import generate_assessment
    with app.app_context():
        return generate_assessment(uid)


def test_assessment_get_returns_questions(client, app):
    uid = _make_user(app, "assess_get@demo.com")
    c = _login(app.test_client(), "assess_get@demo.com")
    r = c.get("/assessment/")          # 路由带尾斜杠
    assert r.status_code == 200
    body = r.get_data(as_text=True)
    assert "Assessment" in body or "评估" in body
    assert "vocab" in body and "speaking" in body


def test_assessment_submit_builds_profile_and_snapshot(client, app):
    uid = _make_user(app, "assess_sub@demo.com")
    c = _login(app.test_client(), "assess_sub@demo.com")

    qs = _gen(app, uid)
    assert len(qs) >= 8, "应覆盖六个技能维度"

    data = {"questions": json.dumps(qs)}
    for q in qs:
        data[q["id"]] = q["answer_index"] if q["kind"] == "mcq" else 5

    r = c.post("/assessment/", data=data)
    assert r.status_code in (302, 303)

    with app.app_context():
        prof = LearnerProfile.query.filter_by(user_id=uid).first()
        assert prof is not None, "LearnerProfile 应被写入"
        assert prof.overall_level in ("A1", "A2", "B1", "B2", "C1")
        assert prof.overall_level != "A1", "全对应高于最低档"
        assert Assessment.query.filter_by(user_id=uid).count() == 1


def test_assessment_result_page_and_idor(client, app):
    uid = _make_user(app, "assess_res@demo.com")
    c = _login(app.test_client(), "assess_res@demo.com")

    qs = _gen(app, uid)
    data = {"questions": json.dumps(qs)}
    for q in qs:
        data[q["id"]] = q["answer_index"] if q["kind"] == "mcq" else 5
    r = c.post("/assessment/", data=data)
    assert r.status_code in (302, 303)

    with app.app_context():
        aid = Assessment.query.filter_by(user_id=uid).first().id

    r = c.get(f"/assessment/result/{aid}")
    assert r.status_code == 200
    assert "Skill Profile" in r.get_data(as_text=True) or "技能画像" in r.get_data(as_text=True)

    # 另一用户尝试查看 → 403（IDOR 防护）
    _make_user(app, "assess_other@demo.com")
    c2 = _login(app.test_client(), "assess_other@demo.com")
    r2 = c2.get(f"/assessment/result/{aid}")
    assert r2.status_code == 403


def test_assessment_wrong_answers_stay_low(client, app):
    uid = _make_user(app, "assess_low@demo.com")
    c = _login(app.test_client(), "assess_low@demo.com")

    qs = _gen(app, uid)
    data = {"questions": json.dumps(qs)}
    for q in qs:
        if q["kind"] == "mcq":
            data[q["id"]] = (q["answer_index"] + 1) % len(q["options"])
        else:
            data[q["id"]] = 1
    c.post("/assessment/", data=data)
    with app.app_context():
        prof = LearnerProfile.query.filter_by(user_id=uid).first()
        assert prof.overall_level == "A1", "全错应判为最低档 A1"
