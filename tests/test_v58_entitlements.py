"""V5.7 / V5.8 套餐权限（entitlements）测试。

规格：AI Tutor 与 Speaking 属 **PRO**；学习 / 练习 / 复习三档通用。

重点覆盖容易出错的边界：
- 无订阅记录（新用户）必须按 free 处理，不能抛异常；
- 已过期 / 非 active 的订阅不能继续享有 PRO；
- 未登录优先返回 401，而不是「请升级 PRO」的 403（提示要符合真实原因）。
"""
from __future__ import annotations

import os
import sys
import tempfile
from datetime import timedelta

import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

_fd, _PATH = tempfile.mkstemp(suffix=".db")
os.close(_fd)

from app import create_app  # noqa: E402
from config import DevelopmentConfig  # noqa: E402
from extensions import db  # noqa: E402
from models import User  # noqa: E402

FREE_EMAIL = "freeplan@example.com"
PRO_EMAIL = "proplan@example.com"
PW = "PlanTest123456"


class _PlanConfig(DevelopmentConfig):
    DATABASE_URL = f"sqlite:///{_PATH}"
    WTF_CSRF_ENABLED = False


@pytest.fixture(scope="module")
def app():
    application = create_app(_PlanConfig)
    application.config["WTF_CSRF_ENABLED"] = False
    with application.app_context():
        from models import Subscription
        for email, plan in ((FREE_EMAIL, "free"), (PRO_EMAIL, "pro")):
            u = User.query.filter_by(email=email).first()
            if not u:
                u = User(email=email, username=email.split("@")[0], is_admin=False)
                u.set_password(PW)
                db.session.add(u)
                db.session.flush()
            if db.session.get(Subscription, u.id) is None:
                db.session.add(Subscription(user_id=u.id, plan=plan, status="active"))
        db.session.commit()
    yield application
    try:
        os.remove(_PATH)
    except OSError:
        pass


def _login(app, email):
    c = app.test_client()
    r = c.post("/login", data={"email": email, "password": PW})
    assert r.status_code in (302, 303)
    return c


@pytest.fixture
def free_client(app):
    return _login(app, FREE_EMAIL)


@pytest.fixture
def pro_client(app):
    return _login(app, PRO_EMAIL)


# --------------------------------------------------------------------------
# 判定逻辑
# --------------------------------------------------------------------------
def test_ai_tutor_and_speaking_are_pro_only():
    from entitlements import allowed_plans
    assert allowed_plans("ai_tutor") == ("pro",)
    assert allowed_plans("speaking") == ("pro",)


def test_learning_is_free_for_everyone():
    from entitlements import allowed_plans
    assert set(allowed_plans("learning")) == {"free", "premium", "pro"}
    assert set(allowed_plans("practice")) == {"free", "premium", "pro"}


def test_missing_subscription_defaults_to_free(app):
    """新用户没有订阅行，必须按 free 处理，绝不能抛异常。"""
    with app.app_context():
        from models import User
        from entitlements import plan_of
        tmp = User(email="norec@example.com", username="norec")
        tmp.set_password(PW)
        db.session.add(tmp)
        db.session.commit()
        assert plan_of(tmp.id) == "free"


def test_expired_pro_subscription_downgrades_to_free(app):
    """过期订阅不能继续白嫖 PRO。"""
    with app.app_context():
        from models import Subscription, User, utcnow
        from entitlements import plan_of
        u = User(email="expired@example.com", username="expired")
        u.set_password(PW)
        db.session.add(u)
        db.session.flush()
        db.session.add(Subscription(user_id=u.id, plan="pro", status="active",
                                    expires_at=utcnow() - timedelta(days=1)))
        db.session.commit()
        assert plan_of(u.id) == "free"


def test_inactive_subscription_downgrades_to_free(app):
    with app.app_context():
        from models import Subscription, User
        from entitlements import plan_of
        u = User(email="inactive@example.com", username="inactive")
        u.set_password(PW)
        db.session.add(u)
        db.session.flush()
        db.session.add(Subscription(user_id=u.id, plan="pro", status="cancelled"))
        db.session.commit()
        assert plan_of(u.id) == "free"


def test_unknown_plan_value_treated_as_free(app):
    with app.app_context():
        from models import Subscription, User
        from entitlements import plan_of
        u = User(email="weird@example.com", username="weird")
        u.set_password(PW)
        db.session.add(u)
        db.session.flush()
        db.session.add(Subscription(user_id=u.id, plan="diamond", status="active"))
        db.session.commit()
        assert plan_of(u.id) == "free"


# --------------------------------------------------------------------------
# API 行为
# --------------------------------------------------------------------------
def test_free_user_blocked_from_ai_tutor(app, free_client):
    r = free_client.post("/api/ai-tutor/explain", json={"text": "hello"})
    assert r.status_code == 403
    body = r.get_json()
    assert body["ok"] is False
    assert body["error"]["code"] == "plan_required"


def test_pro_user_can_use_ai_tutor(app, pro_client):
    r = pro_client.post("/api/ai-tutor/explain", json={"text": "hello"})
    assert r.status_code == 200
    assert r.get_json()["ok"] is True


def test_free_user_blocked_from_speaking(app, free_client):
    r = free_client.post("/api/speaking/score",
                         json={"text": "hi", "reference": "hi"})
    assert r.status_code == 403
    assert r.get_json()["error"]["code"] == "plan_required"


def test_pro_user_can_use_speaking(app, pro_client):
    r = pro_client.post("/api/speaking/score",
                        json={"text": "hi", "reference": "hi"})
    assert r.status_code == 200


def test_anonymous_gets_401_not_403(app):
    """未登录应收到 401，而不是「请升级 PRO」——提示必须符合真实原因。"""
    anon = app.test_client()
    r1 = anon.post("/api/ai-tutor/explain", json={"text": "hello"})
    r2 = anon.post("/api/speaking/score", json={"text": "hi", "reference": "hi"})
    assert r1.status_code == 401
    assert r2.status_code == 401
    assert r1.get_json()["error"]["code"] == "unauthorized"


def test_stt_status_endpoint_also_gated(app, free_client):
    r = free_client.get("/api/speaking/status")
    assert r.status_code == 403


def test_403_payload_includes_current_and_required_plan(app, free_client):
    """前端要据此展示「当前套餐 → 需要套餐」，所以两边都得给。"""
    r = free_client.post("/api/ai-tutor/explain", json={"text": "hello"})
    details = r.get_json()["error"]["details"]
    assert details["plan"] == "free"
    assert "pro" in details["required"]
