"""V5.9 每日配额（daily quota）测试。

覆盖点（都是容易悄悄出错的地方）：

* **只有成功请求才计数** —— 参数错误（422）不该吃掉用户额度，
  否则用户会因为「打错一次字」白白少一次机会。
* **配额按功能独立** —— 用 AI Tutor 不能消耗 Speaking 的额度。
* **PRO 完全不走配额** —— 已付费用户不能因为计数表抖动被误伤。
* **超限与套餐不足是两个错误码** —— 前端提示语不同（「升级」vs「明天再来」）。
* **计数表异常时放行** —— 宁可少收钱，不能砸服务。
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

QUOTA_EMAIL = "quotatest@example.com"
PRO_EMAIL = "quotapro@example.com"
PW = "QuotaTest123456"


class _QuotaConfig(DevelopmentConfig):
    DATABASE_URL = f"sqlite:///{_PATH}"
    WTF_CSRF_ENABLED = False


@pytest.fixture(scope="module")
def app():
    application = create_app(_QuotaConfig)
    application.config["WTF_CSRF_ENABLED"] = False
    with application.app_context():
        from models import Subscription
        for email, plan in ((QUOTA_EMAIL, "free"), (PRO_EMAIL, "pro")):
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
    return _login(app, QUOTA_EMAIL)


@pytest.fixture
def pro_client(app):
    return _login(app, PRO_EMAIL)


@pytest.fixture(autouse=True)
def _clean_usage(app):
    """每个用例从干净额度开始，避免用例之间互相吃额度。"""
    with app.app_context():
        from models import UsageCounter
        UsageCounter.query.delete()
        db.session.commit()
    yield
    with app.app_context():
        from models import UsageCounter
        UsageCounter.query.delete()
        db.session.commit()


def _uid(app, email):
    with app.app_context():
        return User.query.filter_by(email=email).first().id


# --------------------------------------------------------------------------
# 判定逻辑
# --------------------------------------------------------------------------
def test_daily_limits_defined_for_gated_features(app):
    from entitlements import daily_limit
    assert daily_limit("ai_tutor", "free") == 3
    assert daily_limit("ai_tutor", "premium") == 20
    assert daily_limit("ai_tutor", "pro") is None          # PRO 不限量
    assert daily_limit("speaking", "free") == 5
    # 未纳入配额管理的功能：不限量
    assert daily_limit("learning", "free") is None


def test_pro_bypasses_quota_entirely(app):
    """PRO 由套餐门放行，不读配额表。"""
    with app.app_context():
        from entitlements import check
        v = check("ai_tutor", _uid(app, PRO_EMAIL))
        assert v["allowed"] is True
        assert v["by_plan"] is True and v["by_quota"] is False
        assert v["code"] is None


def test_free_user_within_quota_is_allowed_by_quota(app):
    """低档用户是「临时试用」，不是「获得授权」——by_quota 必须可区分。"""
    with app.app_context():
        from entitlements import check
        v = check("ai_tutor", _uid(app, QUOTA_EMAIL))
        assert v["allowed"] is True
        assert v["by_plan"] is False and v["by_quota"] is True
        assert v["quota"]["remaining"] == 3


def test_quota_exhausted_returns_dedicated_code(app):
    with app.app_context():
        from entitlements import check, consume
        uid = _uid(app, QUOTA_EMAIL)
        for _ in range(3):
            consume(uid, "ai_tutor")
        v = check("ai_tutor", uid)
        assert v["allowed"] is False
        assert v["code"] == "quota_exceeded"       # 不是 plan_required
        assert v["hint"] != v.get("plan")           # 提示语要讲「明天再来」
        assert v["quota"]["remaining"] == 0


def test_quota_is_per_feature(app):
    with app.app_context():
        from entitlements import check, consume
        uid = _uid(app, QUOTA_EMAIL)
        for _ in range(5):                     # 把 speaking 额度打满
            consume(uid, "speaking")
        assert check("speaking", uid)["allowed"] is False
        assert check("ai_tutor", uid)["allowed"] is True   # 互不影响


def test_consume_increments_and_reports(app):
    with app.app_context():
        from entitlements import consume, used_today
        uid = _uid(app, QUOTA_EMAIL)
        assert used_today(uid, "ai_tutor") == 0
        assert consume(uid, "ai_tutor") == 1
        assert consume(uid, "ai_tutor") == 2
        assert used_today(uid, "ai_tutor") == 2


def test_today_is_utc_day_string(app):
    """配额重置口径必须是 UTC 自然日，不能随部署地时区漂移。"""
    from datetime import datetime, timezone
    from entitlements import today
    assert today() == datetime.now(timezone.utc).strftime("%Y-%m-%d")
    assert len(today()) == 10


def test_purge_old_usage_keeps_today(app):
    with app.app_context():
        from models import UsageCounter
        from entitlements import purge_old_usage, today
        uid = _uid(app, QUOTA_EMAIL)
        db.session.add(UsageCounter(user_id=uid, feature="ai_tutor", day="2000-01-01", count=9))
        db.session.add(UsageCounter(user_id=uid, feature="ai_tutor", day=today(), count=1))
        db.session.commit()
        n = purge_old_usage(days=30)
        assert n == 1
        assert UsageCounter.query.count() == 1
        assert UsageCounter.query.first().day == today()


# --------------------------------------------------------------------------
# API 行为
# --------------------------------------------------------------------------
def test_successful_calls_consume_quota(free_client, app):
    r = free_client.post("/api/ai-tutor/explain", json={"text": "hello"})
    assert r.status_code == 200
    q = r.get_json()["data"]["quota"]
    assert q["used"] == 1 and q["remaining"] == 2


def test_failed_calls_do_not_consume_quota(free_client, app):
    """参数错误不该吃掉用户额度。"""
    r = free_client.post("/api/ai-tutor/explain", json={"text": "   "})
    assert r.status_code == 422
    with app.app_context():
        from entitlements import used_today
        assert used_today(_uid(app, QUOTA_EMAIL), "ai_tutor") == 0

    # 口语同理：缺少 reference 返回 422，额度不变
    r2 = free_client.post("/api/speaking/score", json={"text": "hi"})
    assert r2.status_code == 422
    with app.app_context():
        from entitlements import used_today
        assert used_today(_uid(app, QUOTA_EMAIL), "speaking") == 0


def test_quota_exhausted_over_http(free_client):
    """成功响应里 ``error`` 恒为 None，所以只能用 ``or {}`` 兜底再取 code。"""
    codes = []
    for _ in range(4):
        r = free_client.post("/api/ai-tutor/explain", json={"text": "hello"})
        codes.append((r.status_code, (r.get_json().get("error") or {}).get("code")))
    assert codes[:3] == [(200, None)] * 3
    assert codes[3] == (403, "quota_exceeded")


def test_status_and_listening_are_free_queries(free_client):
    """纯查询端点不消耗额度 —— 否则刷新页面就在烧次数。"""
    r = free_client.get("/api/speaking/status")
    assert r.status_code == 200
    assert "quota" in r.get_json()["data"]
    assert r.get_json()["data"]["quota"]["remaining"] == 5   # speaking 额度未变


def test_pro_client_unlimited(pro_client):
    for _ in range(8):
        r = pro_client.post("/api/ai-tutor/explain", json={"text": "hello"})
        assert r.status_code == 200
        assert r.get_json()["data"]["quota"]["unlimited"] is True


def test_quota_survives_day_rollover(app, free_client):
    """跨 UTC 日后额度应重置 —— 直接改 day 字段模拟，不真的等一天。"""
    with app.app_context():
        from models import UsageCounter
        from entitlements import consume, quota_state
        uid = _uid(app, QUOTA_EMAIL)
        for _ in range(5):                       # speaking 的 free 额度就是 5
            consume(uid, "speaking")
        assert quota_state("speaking", uid)["remaining"] == 0
        # 当成昨天的记录
        UsageCounter.query.filter_by(user_id=uid).update({"day": "2000-01-01"})
        db.session.commit()
        assert quota_state("speaking", uid)["remaining"] == 5


def test_usage_counter_unique_per_user_feature_day(app):
    """同一天同一功能只能有一行 —— 否则计数会被悄悄分散到多行。"""
    with app.app_context():
        from models import UsageCounter
        from entitlements import consume, today
        uid = _uid(app, QUOTA_EMAIL)
        consume(uid, "ai_tutor")
        consume(uid, "ai_tutor")
        assert UsageCounter.query.filter_by(
            user_id=uid, feature="ai_tutor", day=today()).count() == 1


def test_quota_read_failure_fails_open(app, monkeypatch):
    """计数表炸了也要放行：不能让已付费功能因为旁路故障而不可用。

    注意 patch 的是**数据访问层**（``UsageCounter.query``）而不是
    ``entitlements.used_today`` —— 后者内部的 try/except 才是要验证的对象，
    直接替换它等于把被测代码换掉了。
    """
    with app.app_context():
        import entitlements
        from entitlements import check

        class _BoomQuery:
            @property
            def query(self):  # 模拟「计数表不可读」
                raise RuntimeError("db down")

        monkeypatch.setattr(entitlements, "UsageCounter", _BoomQuery)
        uid = _uid(app, PRO_EMAIL)
        assert check("ai_tutor", uid)["allowed"] is True
        # 异常被吞掉后按 0 处理，前端看到「额度没用过」而不是报错
        assert entitlements.used_today(uid, "ai_tutor") == 0


def test_anonymous_still_401_before_quota(app):
    """登录检查必须在配额之前 —— 未登录不该看到「今日额度」这种提示。"""
    anon = app.test_client()
    r = anon.post("/api/ai-tutor/explain", json={"text": "hello"})
    assert r.status_code == 401
    assert r.get_json()["error"]["code"] == "unauthorized"


def test_expired_pro_falls_back_to_quota(app):
    """过期的 PRO 掉回 free；此时应走配额，而不是直接拒绝。"""
    with app.app_context():
        from models import Subscription, User, utcnow
        from entitlements import check, plan_of
        u = User(email="expiredquota@example.com", username="expq")
        u.set_password(PW)
        db.session.add(u)
        db.session.flush()
        db.session.add(Subscription(user_id=u.id, plan="pro", status="active",
                                    expires_at=utcnow() - timedelta(days=1)))
        db.session.commit()
        assert plan_of(u.id) == "free"
        v = check("ai_tutor", u.id)
        assert v["allowed"] is True and v["by_quota"] is True
