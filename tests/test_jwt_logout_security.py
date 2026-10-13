"""JWT 登出撤销安全回归测试（PolyGo / V6.2 安全加固）。

对应安全要求（本次修复任务）：
  1. 禁止 verify_signature=False 解码未验签 Token 后写入撤销表；
  2. 过期 Refresh Token 必须继续验证签名，仅按安全策略关闭过期检查；
  3. 校验 typ / sub / jti / exp / 算法白名单；
  4. 防伪造 Token、任意 jti 写入、越权撤销他人 Token；
  5. 重复登出、重复刷新、串行/并发刷新安全；
  6. 微信登录、Access/Refresh、Flask Web 登录不受影响。

运行：python tests/test_jwt_logout_security.py
"""
from __future__ import annotations

import base64
import json
import os
import sys
import time

BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)
sys.path.insert(0, os.path.dirname(__file__))

import jwt as pyjwt  # noqa: E402

from config import TestingConfig  # noqa: E402
from app import create_app  # noqa: E402
from extensions import db  # noqa: E402
from models import User  # noqa: E402
from test_flow import C  # noqa: E402

import wx_api  # noqa: E402
import wx_auth  # noqa: E402

PASS = FAIL = 0
SECRET = "jwt-logout-test-secret-0001"


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
    app.config["WX_TOKEN_SECRET"] = SECRET
    app.config["WX_QUIZ_STATE_SECRET"] = "jwt-logout-quiz-secret"
    app.config["WX_APPID"] = "wx_jwt_logout_appid"
    app.config["WX_AUTO_REGISTER"] = True
    with app.app_context():
        db.create_all()
    return app


def auth_headers(tokens: dict) -> dict:
    return {"Authorization": f"Bearer {tokens['access_token']}"}


class _FakeWx:
    def __call__(self, code, timeout=8.0):
        if code in ("victim", "attacker"):
            return {"ok": True, "openid": f"openid_{code}", "unionid": ""}
        return {"ok": False, "errcode": 40029, "errmsg": "invalid js_code"}


def wx_login(app, code):
    r = C(app).post("/api/wx/v1/auth/wx-login", json={"code": code})
    return (r.get_json() or {}).get("data") or {}


def b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def unsigned_token(payload: dict) -> str:
    """手工拼接一枚未签名 JWT（header.payload. 空签名段）。"""
    header = b64url(json.dumps({"alg": "none", "typ": "JWT"}).encode())
    body = b64url(json.dumps(payload).encode())
    return f"{header}.{body}."


def signed_token(payload: dict, secret: str, alg: str = "HS256") -> str:
    return pyjwt.encode(payload, secret, algorithm=alg)


def read_claims(token: str) -> dict:
    """测试辅助：读取令牌 payload（仅用于取 jti 构造攻击样本）。"""
    return pyjwt.decode(token, options={"verify_signature": False})


def main():
    fake = _FakeWx()
    orig_c2s = wx_api.code2session
    wx_api.code2session = fake
    app = build()

    try:
        with app.app_context():
            # 两个独立账户：victim（受害者）/ attacker（攻击者，已登录）
            victim = wx_login(app, "victim")
            attacker = wx_login(app, "attacker")
            v_refresh = victim["tokens"]["refresh_token"]
            a_tokens = attacker["tokens"]
            victim_id = victim["user"]["id"]
            attacker_id = attacker["user"]["id"]
            v_jti = read_claims(v_refresh)["jti"]

            # ============================================================
            print("\n[A] 核心漏洞复现：未验签/错签名令牌不得写入撤销表")
            # ============================================================
            # 说明：PyJWT 先验签、后查 exp，且 algorithms 白名单会拦住
            # alg=none，因此伪造令牌实际到不了旧代码的降级分支；
            # 但「任何路径都不验签就采信 claims」违反纵深防御原则，
            # 仍按安全要求修复（见 wx_auth.logout_refresh_claims）。

            # A1 未签名令牌：exp 过期（瞄准旧代码降级分支）、sub 指向攻击者
            #    自己、jti 指向受害者的有效 refresh
            forged = unsigned_token({
                "sub": str(attacker_id), "typ": "refresh",
                "jti": v_jti, "iat": 1, "exp": 1,
            })
            a_tokens = wx_login(app, "attacker")["tokens"]   # 全新 access
            r = C(app).post("/api/wx/v1/auth/logout", json={
                "refresh_token": forged}, headers=auth_headers(a_tokens))
            body = r.get_json() or {}
            check("A1 未签名伪造令牌：登出接口不报错",
                  r.status_code == 200, (r.status_code, body))
            check("A1 未签名伪造令牌：不登记撤销（revoked 仅 access）",
                  body.get("data", {}).get("revoked") == ["access"], body)
            r = C(app).post("/api/wx/v1/auth/refresh",
                            json={"refresh_token": v_refresh})
            check("A1 受害者的 refresh 未被越权撤销（仍可刷新）",
                  r.status_code == 200, r.status_code)
            v_refresh = r.get_json()["data"]["tokens"]["refresh_token"]
            v_jti = read_claims(v_refresh)["jti"]

            # A2 错误密钥签名 + exp 过期：同样不得放行
            forged2 = signed_token({
                "sub": str(attacker_id), "typ": "refresh",
                "jti": v_jti, "iat": 1, "exp": 1,
            }, secret="attacker-guessed-secret")
            a_tokens = wx_login(app, "attacker")["tokens"]
            r = C(app).post("/api/wx/v1/auth/logout", json={
                "refresh_token": forged2}, headers=auth_headers(a_tokens))
            body = r.get_json() or {}
            check("A2 错签名令牌：不登记撤销",
                  body.get("data", {}).get("revoked") == ["access"], body)
            r = C(app).post("/api/wx/v1/auth/refresh",
                            json={"refresh_token": v_refresh})
            check("A2 受害者 refresh 仍未被他人的错签名令牌撤销",
                  r.status_code == 200, r.status_code)
            v_refresh = r.get_json()["data"]["tokens"]["refresh_token"]
            v_jti = read_claims(v_refresh)["jti"]

            # A3 alg=none 显式攻击
            none_tok = signed_token({
                "sub": str(attacker_id), "typ": "refresh",
                "jti": v_jti, "iat": 1, "exp": 1,
            }, secret="", alg="none")
            a_tokens = wx_login(app, "attacker")["tokens"]
            r = C(app).post("/api/wx/v1/auth/logout", json={
                "refresh_token": none_tok}, headers=auth_headers(a_tokens))
            body = r.get_json() or {}
            check("A3 alg=none 令牌：不登记撤销",
                  body.get("data", {}).get("revoked") == ["access"], body)
            r = C(app).post("/api/wx/v1/auth/refresh",
                            json={"refresh_token": v_refresh})
            check("A3 alg=none 攻击后受害者 refresh 仍有效",
                  r.status_code == 200, r.status_code)
            v_refresh = r.get_json()["data"]["tokens"]["refresh_token"]
            v_jti = read_claims(v_refresh)["jti"]

            # ============================================================
            print("\n[B] 合法路径与声明校验")
            # ============================================================

            # B1 合法 refresh 登出 → 撤销生效（同一用户的新令牌对）
            victim = wx_login(app, "victim")
            v_tokens = victim["tokens"]
            r = C(app).post("/api/wx/v1/auth/logout", json={
                "refresh_token": v_tokens["refresh_token"]},
                headers=auth_headers(v_tokens))
            body = r.get_json() or {}
            check("B1 合法 refresh 登出：access+refresh 均撤销",
                  body.get("data", {}).get("revoked") == ["access", "refresh"], body)
            r = C(app).post("/api/wx/v1/auth/refresh",
                            json={"refresh_token": v_tokens["refresh_token"]})
            check("B1 登出后该 refresh 立即失效", r.status_code == 401,
                  r.status_code)

            # B2 签名有效但已过期的 refresh：按策略允许撤销
            v_tokens = wx_login(app, "victim")["tokens"]
            now = int(time.time())
            expired = signed_token({
                "sub": str(victim_id), "typ": "refresh",
                "jti": "expired-but-signed-0001", "iat": now - 7200,
                "exp": now - 3600,
            }, secret=SECRET)
            r = C(app).post("/api/wx/v1/auth/logout", json={
                "refresh_token": expired}, headers=auth_headers(v_tokens))
            body = r.get_json() or {}
            check("B2 签名有效但过期的 refresh 允许撤销（验签+关过期检查）",
                  body.get("data", {}).get("revoked") == ["access", "refresh"], body)
            with app.app_context():
                from models import WxTokenRevocation
                row = db.session.get(WxTokenRevocation, "expired-but-signed-0001")
            check("B2 过期令牌的 jti 已登记（签名有效为前提）", row is not None)

            # B3 access 令牌当 refresh 提交 → typ 不符，拒绝
            v_tokens = wx_login(app, "victim")["tokens"]
            r = C(app).post("/api/wx/v1/auth/logout", json={
                "refresh_token": v_tokens["access_token"]},
                headers=auth_headers(v_tokens))
            body = r.get_json() or {}
            check("B3 access 令牌冒充 refresh 被拒绝",
                  body.get("data", {}).get("revoked") == ["access"], body)

            # B4 他人有效 refresh → sub 归属不符，拒绝
            v_tokens = wx_login(app, "victim")["tokens"]
            attacker = wx_login(app, "attacker")
            a_tokens = attacker["tokens"]
            r = C(app).post("/api/wx/v1/auth/logout", json={
                "refresh_token": a_tokens["refresh_token"]},
                headers=auth_headers(v_tokens))
            body = r.get_json() or {}
            check("B4 他人有效 refresh 被拒绝（归属校验）",
                  body.get("data", {}).get("revoked") == ["access"], body)
            r = C(app).post("/api/wx/v1/auth/refresh",
                            json={"refresh_token": a_tokens["refresh_token"]})
            check("B4 他人 refresh 未被误撤销", r.status_code == 200,
                  r.status_code)

            # B5 缺声明 / 垃圾输入
            v_tokens = wx_login(app, "victim")["tokens"]
            now = int(time.time())
            missing_jti = signed_token({
                "sub": str(victim_id), "typ": "refresh",
                "iat": now, "exp": now + 600,
            }, secret=SECRET)
            missing_exp = signed_token({
                "sub": str(victim_id), "typ": "refresh",
                "jti": "no-exp-0001", "iat": now,
            }, secret=SECRET)
            bad_sub = signed_token({
                "sub": "not-a-number", "typ": "refresh",
                "jti": "bad-sub-0001", "iat": now, "exp": now + 600,
            }, secret=SECRET)
            for name, tok in (("缺 jti", missing_jti),
                              ("缺 exp", missing_exp),
                              ("sub 非用户 id", bad_sub),
                              ("垃圾字符串", "not-a-jwt-at-all")):
                v_tokens = wx_login(app, "victim")["tokens"]
                r = C(app).post("/api/wx/v1/auth/logout", json={
                    "refresh_token": tok}, headers=auth_headers(v_tokens))
                body = r.get_json() or {}
                check(f"B5 {name}：不登记撤销",
                      body.get("data", {}).get("revoked") == ["access"], body)

            # ============================================================
            print("\n[C] 重复登出 / 重复刷新 / 并发刷新安全")
            # ============================================================

            # C1 重复登出安全（RFC 7009 幂等精神）：
            #   C1a 同一对令牌二次登出 → access 已失效，401 token_revoked，
            #        不 500、无副作用（这是正确语义，不是缺陷）；
            #   C1b 换新 access + 已撤销的 refresh 再登出 → 200 幂等，
            #        重复登记同一 jti 不炸错、不误撤销他人。
            victim = wx_login(app, "victim")
            v_tokens = victim["tokens"]
            r1 = C(app).post("/api/wx/v1/auth/logout", json={
                "refresh_token": v_tokens["refresh_token"]},
                headers=auth_headers(v_tokens))
            r2 = C(app).post("/api/wx/v1/auth/logout", json={
                "refresh_token": v_tokens["refresh_token"]},
                headers=auth_headers(v_tokens))
            body2 = r2.get_json() or {}
            check("C1a 首次登出成功", r1.status_code == 200, r1.status_code)
            check("C1a 同令牌二次登出：401 token_revoked（不 500、无副作用）",
                  r2.status_code == 401
                  and body2.get("error", {}).get("code") == "token_revoked",
                  (r2.status_code, body2))
            v_tokens = wx_login(app, "victim")["tokens"]
            r3 = C(app).post("/api/wx/v1/auth/logout", json={
                "refresh_token": v_tokens["refresh_token"]},
                headers=auth_headers(v_tokens))
            # r4：换新 access，再次提交**同一枚已撤销的 refresh**
            r4 = C(app).post("/api/wx/v1/auth/logout", json={
                "refresh_token": v_tokens["refresh_token"]},
                headers=auth_headers(wx_login(app, "victim")["tokens"]))
            body4 = r4.get_json() or {}
            check("C1b 新 access + 已撤销 refresh 再登出：200 幂等",
                  r3.status_code == 200 and r4.status_code == 200,
                  (r3.status_code, r4.status_code))
            check("C1b 重复撤销不炸错（返回值照常，无 500）",
                  body4.get("ok") is True, body4)
            with app.app_context():
                from models import WxTokenRevocation
                rows = WxTokenRevocation.query.filter_by(
                    jti=read_claims(v_tokens["refresh_token"])["jti"]).all()
            check("C1b 重复撤销同一 jti 在表中仅一行（幂等无重复插入）",
                  len(rows) == 1, len(rows))

            # C2 重复刷新：第一次成功，第二次 401 token_revoked
            victim = wx_login(app, "victim")
            v_refresh = victim["tokens"]["refresh_token"]
            r1 = C(app).post("/api/wx/v1/auth/refresh",
                             json={"refresh_token": v_refresh})
            r2 = C(app).post("/api/wx/v1/auth/refresh",
                             json={"refresh_token": v_refresh})
            body2 = r2.get_json() or {}
            check("C2 首次刷新成功（旋转）", r1.status_code == 200,
                  r1.status_code)
            check("C2 旧 refresh 重复刷新被拒 401 token_revoked",
                  r2.status_code == 401
                  and body2.get("error", {}).get("code") == "token_revoked",
                  (r2.status_code, body2))
            check("C2 旋转后拿到的是新 jti",
                  read_claims(r1.get_json()["data"]["tokens"]["refresh_token"])["jti"]
                  != read_claims(v_refresh)["jti"])

            # C3 用 refresh 令牌访问受保护接口 → 类型不匹配拒绝
            r = C(app).get("/api/wx/v1/auth/me",
                           headers={"Authorization":
                                    f"Bearer {v_refresh}"})
            check("C3 refresh 令牌不能当 access 用（typ 校验）",
                  r.status_code == 401, r.status_code)

            # C4 并发刷新：同一 refresh 多线程同时刷新，只能有一个成功
            #    （claim_token 原子占用，杜绝旧令牌被重放签发两套令牌）
            # 注意：必须在 create_app **之前**把库配成文件库——内存库 +
            # StaticPool 会让多线程共享单连接，事务交错，测不出真实并发。
            import tempfile
            import threading
            tmpdir = tempfile.mkdtemp(prefix="jwt_conc_")
            db_file = os.path.join(tmpdir, "conc.db")

            class _ConcConfig(TestingConfig):
                DATABASE_URL = f"sqlite:///{db_file}"

            conc_app = create_app(_ConcConfig)
            conc_app.config["WTF_CSRF_ENABLED"] = False
            conc_app.config["SERVER_NAME"] = None
            conc_app.config["WX_TOKEN_SECRET"] = SECRET
            conc_app.config["WX_QUIZ_STATE_SECRET"] = "jwt-logout-quiz-secret"
            conc_app.config["WX_APPID"] = "wx_jwt_conc_appid"
            conc_app.config["WX_AUTO_REGISTER"] = True
            with conc_app.app_context():
                db.create_all()
                from seeds.seed_words import seed_from_json
                seed_from_json(conc_app.config["WORDS_JSON"])
            conc_orig_c2s = wx_api.code2session
            wx_api.code2session = _FakeWx()
            try:
                victim = wx_login(conc_app, "victim")
                shared_refresh = victim["tokens"]["refresh_token"]
                results = []
                lock = threading.Lock()

                def do_refresh():
                    try:
                        r = C(conc_app).post(
                            "/api/wx/v1/auth/refresh",
                            json={"refresh_token": shared_refresh})
                        # 注意：ok() 响应里 error 键显式为 None，
                        # 必须 `or {}` 兜底，不能依赖 .get 的默认值
                        code = ((r.get_json() or {}).get("error") or {})\
                            .get("code")
                        item = (r.status_code, code)
                    except Exception as exc:  # noqa: BLE001  线程异常也要记账
                        import traceback
                        traceback.print_exc()
                        item = ("EXC", repr(exc)[:200])
                    with lock:
                        results.append(item)

                threads = [threading.Thread(target=do_refresh)
                           for _ in range(4)]
                for t in threads:
                    t.start()
                for t in threads:
                    t.join(timeout=30)
                ok_count = sum(1 for s, _ in results if s == 200)
                revoked_count = sum(
                    1 for s, c in results
                    if s == 401 and c == "token_revoked")
                check("C4 并发刷新 4 线程：恰好 1 个成功",
                      ok_count == 1, results)
                check("C4 其余 3 个被拒 401 token_revoked（无异常无 500）",
                      revoked_count == 3 and len(results) == 4, results)
                # 旧 refresh 在并发胜出后也不可再用（旋转链只认一次）
                r = C(conc_app).post("/api/wx/v1/auth/refresh",
                                     json={"refresh_token": shared_refresh})
                check("C4 旧 refresh 事后不可再用", r.status_code == 401,
                      r.status_code)
            finally:
                wx_api.code2session = conc_orig_c2s
                try:
                    os.remove(db_file)
                    os.rmdir(tmpdir)
                except OSError:
                    pass

            # ============================================================
            print("\n[D] 既有功能不受影响")
            # ============================================================

            # D1 微信登录闭环：登录 → me → 登出 → 全部失效
            victim = wx_login(app, "victim")
            v_tokens = victim["tokens"]
            r = C(app).get("/api/wx/v1/auth/me",
                           headers=auth_headers(v_tokens))
            check("D1 登录后可访问 /auth/me", r.status_code == 200,
                  r.status_code)
            r = C(app).post("/api/wx/v1/auth/logout", json={
                "refresh_token": v_tokens["refresh_token"]},
                headers=auth_headers(v_tokens))
            check("D1 登出成功", r.status_code == 200, r.status_code)
            r = C(app).get("/api/wx/v1/auth/me",
                           headers=auth_headers(v_tokens))
            check("D1 登出后 access 失效", r.status_code == 401,
                  r.status_code)

            # D2 Flask Web 登录不受影响
            with app.app_context():
                web_user = User(email="jwt_web_login@example.com",
                                username="Web 登录回归")
                web_user.set_password("WebPw123456")
                db.session.add(web_user)
                db.session.commit()
            r = C(app).post("/login", data={
                "email": "jwt_web_login@example.com",
                "password": "WebPw123456",
            }, follow_redirects=False)
            check("D2 Flask Web 登录正常（302）", r.status_code == 302,
                  r.status_code)
            r = C(app).get("/login")
            check("D2 Web /login 页可访问", r.status_code in (200, 302),
                  r.status_code)

            # D3 撤销表清理仍可用
            with app.app_context():
                n = wx_auth.purge_expired_revocations()
            check("D3 purge_expired_revocations 可执行（返回 int）",
                  isinstance(n, int), n)
    finally:
        wx_api.code2session = orig_c2s

    print(f"\n===== JWT 登出安全测试: {PASS} 通过 / {FAIL} 失败 =====")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
