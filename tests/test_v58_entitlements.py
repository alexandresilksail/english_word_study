"""V5.7 / V5.8 套餐权限（entitlements）测试。

规格：AI Tutor 与 Speaking 的**无限使用权属 PRO**；学习 / 练习 / 复习三档通用。
低档套餐另有每日试用额度，见 ``tests/test_v59_quota.py``。

重点覆盖容易出错的边界：
- 无订阅记录（新用户）必须按 free 处理，不能抛异常；
- 已过期 / 非 active 的订阅不能继续享有 PRO；
- 未登录优先返回 401，而不是「请升级 PRO」的 403（提示要符合真实原因）。

.. note:: 本文件只关心「套餐门」。低档用户现在有每日试用额度，
   因此「free 能否调用」要看额度是否用完 —— 需要制造 403 的用例统一用
   ``_exhaust`` 把当天额度打满，避免依赖「free 一定被拒」这一旧假设。
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
    IP_RATELIMIT_ENABLED = False          # 测试共用 127.0.0.1，关闭以免误拦整批登录


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


@pytest.fixture(autouse=True)
def _clean_usage(app):
    """每个用例从干净额度开始：free 用户已有每日试用额度，
    不清理的话用例之间会互相吃掉额度，导致 403 断言随机失败。"""
    with app.app_context():
        from models import UsageCounter
        UsageCounter.query.delete()
        db.session.commit()
    yield
    with app.app_context():
        from models import UsageCounter
        UsageCounter.query.delete()
        db.session.commit()


def _exhaust(app, email, feature):
    """把某用户当天在某功能上的额度打满，用于制造 403 场景。"""
    with app.app_context():
        from entitlements import daily_limit, plan_of
        from models import UsageCounter, User
        uid = User.query.filter_by(email=email).first().id
        limit = daily_limit(feature, plan_of(uid))
        if limit is None:
            return
        db.session.add(UsageCounter(user_id=uid, feature=feature,
                                    day=__import__("entitlements").today(),
                                    count=int(limit)))
        db.session.commit()


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
def test_pro_user_can_use_ai_tutor(app, pro_client):
    r = pro_client.post("/api/ai-tutor/explain", json={"text": "hello"})
    assert r.status_code == 200
    assert r.get_json()["ok"] is True


def test_free_user_blocked_from_ai_tutor_once_quota_runs_out(app, free_client):
    """free 档有每日试用额度；用尽后才拒绝，错误码是 quota_exceeded。

    与 plan_required 区分开：前者提示「明天再来 / 升级」，后者提示「升级」。
    """
    _exhaust(app, FREE_EMAIL, "ai_tutor")
    r = free_client.post("/api/ai-tutor/explain", json={"text": "hello"})
    assert r.status_code == 403
    body = r.get_json()
    assert body["ok"] is False
    assert body["error"]["code"] == "quota_exceeded"


def test_free_user_blocked_from_speaking_once_quota_runs_out(app, free_client):
    _exhaust(app, FREE_EMAIL, "speaking")
    r = free_client.post("/api/speaking/score",
                         json={"text": "hi", "reference": "hi"})
    assert r.status_code == 403
    assert r.get_json()["error"]["code"] == "quota_exceeded"


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
    """/status 是查询端点，但仍要过门禁：额度用完时同样返回 403。"""
    _exhaust(app, FREE_EMAIL, "speaking")
    r = free_client.get("/api/speaking/status")
    assert r.status_code == 403


def test_403_payload_includes_current_and_required_plan(app, free_client):
    """前端要据此展示「当前套餐 → 需要套餐」，所以两边都得给。"""
    _exhaust(app, FREE_EMAIL, "ai_tutor")
    r = free_client.post("/api/ai-tutor/explain", json={"text": "hello"})
    details = r.get_json()["error"]["details"]
    assert details["plan"] == "free"
    assert "pro" in details["required"]
    # 还要给出额度快照，前端才能显示「今日 3/3 已用完」
    assert details["quota"]["limit"] == 3
    assert details["quota"]["remaining"] == 0
