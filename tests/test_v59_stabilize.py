"""V5.9 稳定化回归：IP 限流接入、admin 批量聚合（去 N+1）、dashboard_stats 缓存。

仅验证「收口后行为正确」，不对内部实现做脆弱断言。
"""
from __future__ import annotations

import pytest

from extensions import db
from models import User

PW = "V59testPass1!"


@pytest.fixture
def admin_client(app):
    with app.app_context():
        u = User(email="admin_v59@demo.com", username="adminv59", is_admin=True)
        u.set_password(PW)
        db.session.add(u)
        db.session.commit()
    c = app.test_client()
    r = c.post("/login", data={"email": "admin_v59@demo.com", "password": PW})
    assert r.status_code in (302, 303)
    return c


def test_ip_rate_limit_blocks_after_threshold(client, app):
    """HIGH-1：ip_too_many_requests() 此前从未被调用，现在必须真正拦截爆破。

    测试配置默认关闭限流（IP_RATELIMIT_ENABLED=False，避免共用 127.0.0.1 误拦），
    这里临时开启以验证「开启时」的生产行为；用独立 TEST-NET-3 IP 隔离其它测试计数。
    """
    app.config["IP_RATELIMIT_ENABLED"] = True
    try:
        ip = "203.0.113.99"  # TEST-NET-3，隔离其它测试，避免复用同一 IP 计数
        statuses = []
        for _ in range(21):
            r = client.post(
                "/auth/code/request",
                json={"email": "ratelimit@demo.com", "purpose": "login"},
                headers={"X-Forwarded-For": ip},
            )
            statuses.append(r.status_code)
        assert statuses[0] != 429, "限流不应从第一次请求就生效"
        assert statuses[20] == 429, f"第 21 次应被 IP 限流拦截，实际: {statuses[20]}"
    finally:
        app.config["IP_RATELIMIT_ENABLED"] = False


def test_ip_rate_limit_only_on_post(client):
    """GET 不应被限流闸门拦截（只在 POST 到高频面时计数）。"""
    ip = "203.0.113.100"
    r = client.get("/login", headers={"X-Forwarded-For": ip})
    assert r.status_code != 429


def test_dashboard_stats_cache_returns_same_object(app):
    """HIGH-4a：TTL 内应命中缓存，避免每个认证请求打十余条 count 查询。"""
    with app.app_context():
        u = User(email="statscache@demo.com", username="statscache")
        u.set_password(PW)
        db.session.add(u)
        db.session.commit()
        from services import dashboard_stats

        a = dashboard_stats(u.id)
        b = dashboard_stats(u.id)
        assert a is b, "TTL 内应命中缓存，返回同一对象"


def test_admin_users_returns_200_and_lists_users(admin_client, app):
    """HIGH-4b：admin.users 重写为批量聚合后，仍能正确返回用户列表（含统计）。"""
    with app.app_context():
        for i in range(3):
            u = User(email=f"v59u{i}@demo.com", username=f"v59u{i}", is_admin=False)
            u.set_password(PW)
            db.session.add(u)
        db.session.commit()

    r = admin_client.get("/admin/users")
    assert r.status_code == 200
    body = r.get_data(as_text=True)
    # 应包含刚创建的用户，且不应出现未捕获异常（500）
    assert "v59u0@demo.com" in body
    assert "v59u2@demo.com" in body
