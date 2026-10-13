"""V6.1 独立审计安全回归测试：认证 / 防爆破 / 限流 / 令牌归属 / 信息泄露。

对应审计发现（详见 交付报告 / 审计报告）：
  P1-1  生产环境验证码失败开放（dev_code 明文下发）→ 已修：email_code._dev_codes_allowed
  P1-2  bind-email / unbind 密码路径无防爆破锁定 → 已修：复用 register_login_failure
  P2-3  限流信任可伪造的 X-Forwarded-For → 已修：_client_ip 优先 X-Real-IP
  P2-5  登出可借道撤销他人令牌（不验签不验归属）→ 已修：logout_refresh_claims
  P2-6  bind-email 错误消息差异导致邮箱枚举 → 已修：统一消息
  P2-7  email_code 每小时限流因删除旧码而失效 → 已修：作废旧码改为置过期

另覆盖 JWT 基础攻击面：alg=none、typ 混淆、超长令牌。

运行：python tests/test_wx_security_audit.py
"""
from __future__ import annotations

import os
import sys

BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)
sys.path.insert(0, os.path.dirname(__file__))

from config import TestingConfig  # noqa: E402
from app import create_app  # noqa: E402
from extensions import db  # noqa: E402
from models import User, Word  # noqa: E402

import email_code  # noqa: E402
import wx_api  # noqa: E402
from test_flow import C  # noqa: E402

PASS = FAIL = 0
PW = "AuditPw918273"


def check(name, cond, extra=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("  PASS  " + name)
    else:
        FAIL += 1
        print("  FAIL  " + name + ("  << " + str(extra) if extra else ""))


def build():
    app = create_app(TestingConfig)
    app.config["WTF_CSRF_ENABLED"] = False
    app.config["SERVER_NAME"] = None
    app.config["WX_TOKEN_SECRET"] = "audit-wx-token-secret-0001"
    app.config["WX_QUIZ_STATE_SECRET"] = "audit-wx-quiz-state-secret"
    app.config["WX_APPID"] = "wx_audit_appid"
    app.config["WX_AUTO_REGISTER"] = True
    with app.app_context():
        db.create_all()
        from seeds.seed_words import seed_from_json
        seed_from_json(app.config["WORDS_JSON"])
    return app


def auth_headers(tokens: dict) -> dict:
    return {"Authorization": f"Bearer {tokens['access_token']}"}


class _FakeWx:
    def __init__(self):
        self.calls = 0

    def __call__(self, code, timeout=8.0):
        self.calls += 1
        if code == "audit_a":
            return {"ok": True, "openid": "openid_audit_a", "unionid": ""}
        if code == "audit_b":
            return {"ok": True, "openid": "openid_audit_b", "unionid": ""}
        return {"ok": False, "errcode": 40029, "errmsg": "invalid js_code"}


def wx_login(app, code):
    r = C(app).post("/api/wx/v1/auth/wx-login", json={"code": code})
    return (r.get_json() or {}).get("data") or {}


def main():
    fake = _FakeWx()
    orig_c2s = wx_api.code2session
    wx_api.code2session = fake
    app = build()

    try:
        with app.app_context():
            # 预置「已有账户」，供绑定 / 爆破场景使用
            existing = User(email="audit_target@example.com", username="审计目标")
            existing.set_password(PW)
            db.session.add(existing)
            db.session.commit()

            print("[1] P1-1 生产环境绝不返回验证码明文")
            orig_send = email_code.send_email
            email_code.send_email = lambda *a, **k: False   # 模拟「未配置 SMTP」
            try:
                # 开发 / 测试环境：允许 dev_code（本地可跑通流程）
                r = C(app).post("/api/wx/v1/auth/send-code",
                                json={"email": "dev_allowed@example.com"})
                body = r.get_json() or {}
                check("测试环境未配 SMTP 时给出 dev_code（本地可自测）",
                      r.status_code == 200 and body.get("data", {}).get("dev_code"), body)

                # 生产形态：TESTING / DEBUG 均关闭 → 必须拒绝下发
                app.config["TESTING"] = False
                app.config["DEBUG"] = False
                r = C(app).post("/api/wx/v1/auth/send-code",
                                json={"email": "prod_blocked@example.com"})
                body = r.get_json() or {}
                check("生产形态未配 SMTP 时拒绝发送（429）", r.status_code == 429, body)
                check("生产形态响应不含 dev_code",
                      "dev_code" not in (body.get("data") or {}), body)
            finally:
                app.config["TESTING"] = True
                app.config["DEBUG"] = False
                email_code.send_email = orig_send

            print("\n[2] P1-2 bind-email 密码路径防爆破（5 次锁定）")
            data_a = wx_login(app, "audit_a")
            tokens_a = data_a["tokens"]
            cli = C(app)
            codes = []
            for i in range(5):
                r = cli.post("/api/wx/v1/auth/bind-email",
                             json={"email": "audit_target@example.com",
                                   "password": "wrong-password"},
                             headers=auth_headers(tokens_a))
                codes.append(r.status_code)
            # 第 5 次失败即达到阈值：当次可能直接返回 429（即时锁定语义）
            check("前 4 次错误密码均返回 401", codes[:4] == [401] * 4, codes)
            check("第 5 次失败触发锁定（401/429 均可）",
                  codes[4] in (401, 429), codes)
            r = cli.post("/api/wx/v1/auth/bind-email",
                         json={"email": "audit_target@example.com", "password": PW},
                         headers=auth_headers(tokens_a))
            body = r.get_json() or {}
            check("第 6 次（即使密码正确）被锁定 429",
                  r.status_code == 429 and body.get("error", {}).get("code") == "locked", body)
            # 解锁，恢复后续测试
            with app.app_context():
                u = User.query.filter_by(email="audit_target@example.com").first()
                u.failed_logins = 0
                u.locked_until = None
                db.session.commit()

            print("\n[3] P1-2 unbind 密码路径防爆破")
            data_b = wx_login(app, "audit_b")
            # 先把 audit_b 绑到已有账户（此时目标账户已解锁），**用绑定返回的新令牌**
            r = C(app).post("/api/wx/v1/auth/bind-email",
                            json={"email": "audit_target@example.com", "password": PW},
                            headers=auth_headers(data_b["tokens"]))
            check("先完成一次正常绑定（供解绑测试）", r.status_code == 200,
                  (r.get_json() or {}))
            tokens_b = (r.get_json() or {}).get("data", {}).get("tokens") or {}
            cli_b = C(app)
            locked_at = None
            for i in range(5):
                r = cli_b.post("/api/wx/v1/auth/unbind",
                               json={"password": "nope-nope"},
                               headers=auth_headers(tokens_b))
                if r.status_code == 429:
                    locked_at = i + 1
                    break
                if r.status_code != 401:
                    check(f"unbind 第 {i + 1} 次错密码应 401", False, r.get_json())
                    break
            else:
                check("unbind 前 5 次错密码均 401", True)
            if locked_at is None:
                check("unbind 错密码 5 次内触发锁定", False, "未出现 429")
            else:
                check(f"unbind 第 {locked_at} 次错密码触发锁定（≤5 次）", True)
            r = cli_b.post("/api/wx/v1/auth/unbind",
                           json={"password": PW},
                           headers=auth_headers(tokens_b))
            body = r.get_json() or {}
            check("锁定期间即使密码正确也被拒绝（429）",
                  r.status_code == 429 and body.get("error", {}).get("code") == "locked", body)
            with app.app_context():
                u = User.query.filter_by(email="audit_target@example.com").first()
                u.failed_logins = 0
                u.locked_until = None
                db.session.commit()

            print("\n[4] P2-3 限流按不可伪造的 X-Real-IP 计数")
            wx_api._login_hits.clear()
            seen = []
            for i in range(35):
                r = C(app).post(
                    "/api/wx/v1/auth/wx-login", json={"code": "invalid"},
                    headers={"X-Real-IP": "7.7.7.7",
                             "X-Forwarded-For": f"10.0.0.{i % 250 + 1}"})
                seen.append(r.status_code)
            check("同一 X-Real-IP 轮换伪造 XFF：前 30 次放行",
                  seen[:30] == [401] * 30, seen[:31])
            check("第 31 次起被限流（429）——伪造 XFF 无法绕过",
                  all(s == 429 for s in seen[30:]), seen[30:])
            wx_api._login_hits.clear()

            print("\n[5] P2-5 登出只撤销属于自己的令牌")
            # A 已登录（tokens_a，占位账户）；B 重新登录拿新令牌
            data_b2 = wx_login(app, "audit_b")
            tokens_b2 = data_b2["tokens"]
            # 先测伪造签名的 refresh：签名无效，绝不能被登记撤销
            import jwt as _jwt
            forged = _jwt.encode(
                {"sub": "1", "typ": "refresh", "jti": "forged-jti-0001",
                 "iat": 1, "exp": 9_999_999_999},
                "wrong-secret", algorithm="HS256")
            r = C(app).post("/api/wx/v1/auth/logout",
                            json={"refresh_token": forged},
                            headers=auth_headers(tokens_a))
            body = r.get_json() or {}
            check("伪造签名的 refresh 不会被登记撤销",
                  body.get("data", {}).get("revoked") == ["access"], body)
            # 上面的登出已撤销 A 的 access，重新登录拿新令牌再测借道撤销
            tokens_a = wx_login(app, "audit_a")["tokens"]
            # 再测借道撤销：A 登出时在 body 里塞 B 的有效 refresh
            r = C(app).post("/api/wx/v1/auth/logout",
                            json={"refresh_token": tokens_b2["refresh_token"]},
                            headers=auth_headers(tokens_a))
            body = r.get_json() or {}
            check("A 登出成功", r.status_code == 200, body)
            check("A 的登出只撤销自己的 access（revoked 不含他人 refresh）",
                  body.get("data", {}).get("revoked") == ["access"], body)
            # B 的 refresh 必须仍然有效
            r = C(app).post("/api/wx/v1/auth/refresh",
                            json={"refresh_token": tokens_b2["refresh_token"]})
            check("B 的 refresh 令牌未被借道撤销（仍可换发）", r.status_code == 200,
                  r.status_code)

            print("\n[6] P2-6 bind-email 不泄露邮箱是否注册")
            r1 = C(app).post("/api/wx/v1/auth/bind-email",
                             json={"email": "no_such_user_zzz@example.com",
                                   "password": "whatever"},
                             headers=auth_headers(tokens_a))
            r2 = C(app).post("/api/wx/v1/auth/bind-email",
                             json={"email": "audit_target@example.com",
                                   "password": "wrong-again"},
                             headers=auth_headers(tokens_a))
            b1, b2 = r1.get_json() or {}, r2.get_json() or {}
            check("邮箱不存在与密码错误：状态码一致",
                  r1.status_code == r2.status_code, (r1.status_code, r2.status_code))
            check("邮箱不存在与密码错误：错误消息逐字一致",
                  b1.get("error", {}).get("message") == b2.get("error", {}).get("message")
                  and b1.get("error", {}).get("code") == b2.get("error", {}).get("code"),
                  (b1.get("error"), b2.get("error")))

            print("\n[7] P2-7 邮箱验证码每小时限流真实生效")
            orig_send = email_code.send_email
            orig_interval = email_code.RESEND_INTERVAL_SEC
            email_code.send_email = lambda *a, **k: True
            email_code.RESEND_INTERVAL_SEC = 0     # 绕过 60s 重发间隔，专测每小时上限
            try:
                with app.app_context():
                    # 同一邮箱连续发 12 次（限流是「同邮箱」维度）
                    results = []
                    for i in range(12):
                        ok, _msg, _dev = email_code.issue_code(
                            "hourly_limit@example.com", "login", ip="127.0.0.1")
                        results.append(ok)
                    check("前 10 次发送放行",
                          results[:10] == [True] * 10, results)
                    check("第 11 次起被每小时上限拦截",
                          results[10:] == [False, False], results)
                    # 交替 purpose 也不能绕过（修复前：旧码被 delete，计数永远 ≤ 2）
                    results2 = []
                    for i in range(12):
                        purpose = "login" if i % 2 == 0 else "register"
                        ok, _m, _d = email_code.issue_code(
                            "alternate_purpose@example.com", purpose, ip="127.0.0.1")
                        results2.append(ok)
                    check("交替 login/register 也无法突破每小时上限",
                          results2[:10] == [True] * 10 and results2[10:] == [False, False],
                          results2)
            finally:
                email_code.RESEND_INTERVAL_SEC = orig_interval
                email_code.send_email = orig_send

            print("\n[8] JWT 基础攻击面")
            import jwt as _jwt2
            # alg=none 伪造
            none_token = _jwt2.encode(
                {"sub": "1", "typ": "access", "jti": "x", "iat": 1,
                 "exp": 9_999_999_999},
                key=None, algorithm="none")
            r = C(app).get("/api/wx/v1/auth/me",
                           headers={"Authorization": f"Bearer {none_token}"})
            check("alg=none 伪造令牌被拒绝", r.status_code == 401, r.status_code)
            # typ 混淆：拿 access 当 refresh 用
            r = C(app).post("/api/wx/v1/auth/refresh",
                            json={"refresh_token": tokens_a["access_token"]})
            check("access 令牌不能当 refresh 使用（typ 校验）",
                  r.status_code == 401 and
                  (r.get_json() or {}).get("error", {}).get("code") in
                  ("invalid_token", "token_revoked"), r.get_json())
            # 超长令牌
            r = C(app).get("/api/wx/v1/auth/me",
                           headers={"Authorization": "Bearer " + "A" * 9000})
            check("超长令牌被拒绝（防解析开销）", r.status_code == 401, r.status_code)
            # 无 Authorization 头
            r = C(app).get("/api/wx/v1/auth/me")
            check("无 Authorization 头 → 401 missing_token",
                  r.status_code == 401 and
                  (r.get_json() or {}).get("error", {}).get("code") == "missing_token",
                  r.get_json())

            print("\n[9] 响应不泄露敏感字段")
            data = wx_login(app, "audit_a")
            raw = str(data)
            check("登录响应不含 session_key", "session_key" not in raw, raw[:200])
            check("登录响应不含 password_hash", "password_hash" not in raw)
            r = C(app).get("/api/wx/v1/auth/me",
                           headers=auth_headers(data["tokens"]))
            me_raw = r.get_data(as_text=True)
            check("/auth/me 不含密码哈希与 session_key",
                  "password_hash" not in me_raw and "session_key" not in me_raw)

    finally:
        wx_api.code2session = orig_c2s

    print("\n" + "=" * 60)
    print(f"结果：通过 {PASS} / 失败 {FAIL}")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
