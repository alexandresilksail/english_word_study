"""邮箱验证码（无密码）注册 / 登录 端到端测试。

用法：
    python tests/test_email_code.py

覆盖：
- 验证码注册（无密码）→ 自动登录 → 账号标记为 passwordless
- 验证码登录（已注册用户）
- 错误验证码 / 过期 / 重复请求限流 / 未注册邮箱
- 原有密码注册 + 密码登录不受影响（不破坏既有用户）
- 无密码账号无法用密码登录
- 验证码一次性：成功后不可复用
- 用户数据隔离
- CSRF：缺少 token 的 AJAX 请求被拒绝
"""
from __future__ import annotations

import os
import re
import sys

BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from app import create_app  # noqa: E402
from config import TestingConfig  # noqa: E402
from extensions import db  # noqa: E402
from models import EmailCode, User, utcnow  # noqa: E402

PASS = FAIL = 0
PW = "Study123456"


def check(name, cond, extra=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("  PASS  " + name)
    else:
        FAIL += 1
        print("  FAIL  " + name + ("  << " + str(extra) if extra else ""))


class CleanWrapper:
    """每次请求前清理 g._login_user，保证多个 client 真正隔离。"""

    def __init__(self, app):
        self.app = app
        self.client = app.test_client()

    def _reset(self):
        try:
            from flask import g
            for key in ("_login_user", "_fresh"):
                if hasattr(g, key):
                    g.pop(key)
        except Exception:
            pass

    def get(self, *a, **k):
        self._reset()
        return self.client.get(*a, **k)

    def post(self, *a, **k):
        self._reset()
        return self.client.post(*a, **k)


def C(app):
    return CleanWrapper(app)


def build(csrf_enabled: bool = False):
    app = create_app(TestingConfig)
    app.config["WTF_CSRF_ENABLED"] = csrf_enabled
    app.config["SERVER_NAME"] = None
    with app.app_context():
        db.create_all()
        from seeds.seed_words import seed_from_json
        seed_from_json(app.config["WORDS_JSON"])
    return app


def csrf_of(html: str) -> str:
    m = re.search(r'name="csrf_token"[^>]*value="([^"]+)"', html)
    return m.group(1) if m else ""


def request_code(c, email: str, purpose: str):
    """申请验证码，返回 (status, json)。"""
    r = c.post("/auth/code/request", json={"email": email, "purpose": purpose})
    try:
        data = r.get_json()
    except Exception:
        data = {}
    return r.status_code, (data or {})


def main():
    app = build(csrf_enabled=False)

    # ---------------------------------------------------------------- 1. 注册
    print("\n[1] 邮箱验证码注册（无密码）")
    with app.app_context():
        c = C(app)
        email = "otp_user@example.com"

        st, data = request_code(c, email, "register")
        check("注册验证码申请成功", st == 200 and data.get("ok"), (st, data))
        code = data.get("dev_code")
        check("返回验证码（未配置 SMTP 降级）", bool(code) and len(str(code)) == 6, code)

        r = c.post("/auth/code/register", data={
            "email": email, "code": code, "username": "OTP Learner",
        }, follow_redirects=False)
        check("验证码注册成功并跳转", r.status_code == 302, r.status_code)

        u = User.query.filter_by(email=email).first()
        check("用户已创建", u is not None)
        check("账号标记为无密码", bool(u and u.is_passwordless))
        check("邮箱已标记为已验证", bool(u and u.email_verified))
        check("用户名为表单填写值", bool(u and u.username == "OTP Learner"), u.username if u else None)

        d = c.get("/dashboard")
        check("注册后处于登录态", d.status_code == 200, d.status_code)
        check("顶部显示用户名", "OTP Learner" in d.get_data(as_text=True))

    # ---------------------------------------------------------------- 2. 登录
    print("\n[2] 邮箱验证码登录")
    with app.app_context():
        c = C(app)
        c.post("/logout")

        st, data = request_code(c, email, "login")
        check("登录验证码申请成功", st == 200 and data.get("ok"), (st, data))
        code = data.get("dev_code")

        r = c.post("/auth/code/login", data={"email": email, "code": code}, follow_redirects=False)
        check("验证码登录跳转", r.status_code == 302, r.status_code)
        dash = c.get("/dashboard")
        check("登录后可访问 Dashboard", dash.status_code == 200, dash.status_code)

        # 验证码一次性（先登出，避免被「已登录 → 跳转 dashboard」掩盖）
        c.post("/logout")
        r2 = c.post("/auth/code/login", data={"email": email, "code": code}, follow_redirects=False)
        check("验证码不可重复使用", r2.status_code == 200, r2.status_code)
        check("重复使用时提示错误", "验证码" in r2.get_data(as_text=True))

    # ---------------------------------------------------------------- 3. 异常
    print("\n[3] 异常与限流")
    with app.app_context():
        c = C(app)

        st, data = request_code(c, "nobody@example.com", "login")
        check("未注册邮箱登录被拒", st == 400 and not data.get("ok"), (st, data))

        st, data = request_code(c, email, "register")
        check("已注册邮箱再注册被拒", st == 400 and not data.get("ok"), (st, data))

        st, data = request_code(c, email, "login")
        check("登录验证码可再次申请", st == 200 and data.get("ok"), (st, data))
        good = data.get("dev_code")

        # 60 秒内重发应被限流
        st2, data2 = request_code(c, email, "login")
        check("60 秒内重发被限流", st2 == 429 and not data2.get("ok"), (st2, data2))

        # 错误验证码
        wrong = "000000" if good != "000000" else "111111"
        r = c.post("/auth/code/login", data={"email": email, "code": wrong}, follow_redirects=False)
        check("错误验证码不登录", r.status_code == 200, r.status_code)
        check("错误验证码给出提示", "不正确" in r.get_data(as_text=True))

        # 正确验证码仍可登录（错误未耗尽）
        r = c.post("/auth/code/login", data={"email": email, "code": good}, follow_redirects=False)
        check("错误后正确验证码仍可登录", r.status_code == 302, r.status_code)

        # 过期验证码
        row = EmailCode(email="exp@example.com", code_hash="x", purpose="login",
                        created_at=utcnow(), expires_at=utcnow())
        db.session.add(row)
        db.session.commit()
        from email_code import verify_code
        ok, msg = verify_code("exp@example.com", "123456", "login")
        check("过期验证码被拒绝", not ok, msg)

    # ---------------------------------------------------------------- 4. 兼容
    print("\n[4] 与原密码体系兼容")
    with app.app_context():
        c = C(app)
        pw_email = "pw_user@example.com"
        page = c.get("/register", query_string={"mode": "password"}).get_data(as_text=True)
        check("密码注册页可访问", "密码" in page)

        r = c.post("/register", data={
            "email": pw_email, "username": "PWUser",
            "password": PW, "confirm": PW,
        }, follow_redirects=False)
        check("密码注册仍然可用", r.status_code == 302, r.status_code)
        c.post("/logout")

        r = c.post("/login", data={"email": pw_email, "password": PW}, follow_redirects=False)
        check("密码登录仍然可用", r.status_code == 302, r.status_code)
        c.post("/logout")

        # 无密码账号不能用密码登录
        r = c.post("/login", data={"email": email, "password": "Guess123456"}, follow_redirects=False)
        check("无密码账号密码登录失败", r.status_code == 200, r.status_code)

    # ---------------------------------------------------------------- 5. 隔离
    print("\n[5] 用户数据隔离")
    with app.app_context():
        c = C(app)
        st, data = request_code(c, email, "login")
        c.post("/auth/code/login", data={"email": email, "code": data.get("dev_code")})
        wrong_page = c.get("/wrong").get_data(as_text=True)
        check("新用户错题本为空", ("错题本是空的" in wrong_page or "No mistakes" in wrong_page))
        check("错题中不含其他用户数据", "PWUser" not in wrong_page)

    # ---------------------------------------------------------------- 6. CSRF
    print("\n[6] CSRF 保护")
    csrf_app = build(csrf_enabled=True)
    with csrf_app.app_context():
        c = C(csrf_app)
        r = c.post("/auth/code/request", json={"email": "x@y.com", "purpose": "login"})
        check("无 CSRF token 的申请被拒绝", r.status_code == 400, r.status_code)

        page = c.get("/login").get_data(as_text=True)
        token = csrf_of(page)
        check("登录页含 CSRF token", bool(token))
        r = c.post("/auth/code/request", json={"email": "csrf_ok@example.com", "purpose": "register"},
                   headers={"X-CSRFToken": token})
        check("带 CSRF token 的请求通过校验（业务放行）", r.status_code == 200, r.status_code)

    print("\n" + "=" * 60)
    print(f"结果：{PASS} 通过 / {FAIL} 失败")
    print("=" * 60)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
