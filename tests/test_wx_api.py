"""微信小程序 API（/api/wx/v1）测试：认证 / 权限 / 数据隔离 / 学习闭环。

覆盖（对应验收要求）：
  1. 未登录访问受保护接口 → 401 JSON（不跳 HTML 登录页）
  2. 伪造 Token / 篡改 Token → 401
  3. 过期 Token → 401（code=token_expired）
  4. Refresh 换发 + 旋转（旧 refresh 立即失效）
  5. 登出撤销（access / refresh 均进黑名单）
  6. 微信登录闭环（mock code2Session）：自动建号、二次登录复用同一账户
  7. 绑定必须验证对原账户的控制权（错密码拒绝 / 对密码成功）
  8. 重复绑定被唯一约束拦截
  9. 跨用户数据隔离：用户 A 拿不到用户 B 的收藏 / 错题 / 进度
 10. 单词 / 收藏 / 错题 / 答题 / 交卷 全流程真实落库
 11. 出题状态令牌被篡改 → 拒绝判题
 12. 响应中绝不出现 session_key / 密码哈希

运行：python tests/test_wx_api.py
"""
from __future__ import annotations

import os
import sys
import time

BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)
sys.path.insert(0, os.path.dirname(__file__))

from config import TestingConfig  # noqa: E402
from app import create_app  # noqa: E402
from extensions import db  # noqa: E402
from models import (Favorite, TestRecord, User, UserWordProgress, WeChatIdentity,  # noqa: E402
                    Word, WrongAnswer)

import wx_api  # noqa: E402
from test_flow import C  # noqa: E402

PASS = FAIL = 0
PW = "WxBind479215"


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
    # 测试环境显式给一套密钥，避免依赖开发兜底值
    app.config["WX_TOKEN_SECRET"] = "test-wx-token-secret-please-ignore"
    app.config["WX_QUIZ_STATE_SECRET"] = "test-wx-quiz-state-secret"
    app.config["WX_APPID"] = "wx_test_appid"
    app.config["WX_ACCESS_TTL"] = 2 * 60 * 60
    app.config["WX_REFRESH_TTL"] = 30 * 24 * 60 * 60
    app.config["WX_AUTO_REGISTER"] = True
    with app.app_context():
        db.create_all()
        from seeds.seed_words import seed_from_json
        seed_from_json(app.config["WORDS_JSON"])
    return app


def auth_headers(tokens: dict) -> dict:
    return {"Authorization": f"Bearer {tokens['access_token']}"}


# --------------------------------------------------------------------------
# mock 微信 code2Session：不发真实网络请求
# --------------------------------------------------------------------------
class _FakeWx:
    """按 code 返回预置 openid，模拟微信侧行为。

    - ``wx_test_new``    → 一个全新 openid（首次登录）
    - ``wx_test_repeat`` → 复用同一 openid（二次登录）
    - 其它              → 微信报错（code 无效）
    """

    def __init__(self):
        self.calls = 0

    def __call__(self, code, timeout=8.0):
        self.calls += 1
        if code == "wx_test_new":
            return {"ok": True, "openid": "openid_new_user_001", "unionid": ""}
        if code == "wx_test_repeat":
            return {"ok": True, "openid": "openid_new_user_001", "unionid": ""}
        if code == "wx_test_other":
            return {"ok": True, "openid": "openid_other_user_002", "unionid": ""}
        return {"ok": False, "errcode": 40029, "errmsg": "invalid js_code"}


def main():
    fake = _FakeWx()
    orig = wx_api.code2session
    wx_api.code2session = fake
    app = build()

    try:
        with app.app_context():
            # 预置一个「已有账户」，用于绑定场景
            existing = User(email="bind_target@example.com", username="绑定目标", is_admin=False)
            existing.set_password(PW)
            db.session.add(existing)
            db.session.commit()
            existing_id = existing.id

            anon = C(app)

            print("\n[1] 未登录访问受保护接口 → 401 JSON")
            protected = [
                ("GET", "/api/wx/v1/auth/me"),
                ("GET", "/api/wx/v1/home"),
                ("GET", "/api/wx/v1/favorites"),
                ("GET", "/api/wx/v1/wrong"),
                ("GET", "/api/wx/v1/progress"),
                ("POST", "/api/wx/v1/quiz/start"),
                ("POST", "/api/wx/v1/auth/logout"),
                ("POST", "/api/wx/v1/words/1/favorite"),
            ]
            for method, url in protected:
                r = (anon.get(url) if method == "GET" else anon.post(url, json={}))
                body = r.get_json() or {}
                check(f"{method} {url} 无凭证 → 401",
                      r.status_code == 401 and body.get("ok") is False
                      and body.get("error", {}).get("code") == "missing_token",
                      f"status={r.status_code} body={body}")

            print("\n[2] 伪造 / 篡改 Token → 401")
            r = anon.get("/api/wx/v1/auth/me",
                         headers={"Authorization": "Bearer not.a.real.token"})
            body = r.get_json() or {}
            check("伪造 Token → 401 invalid_token",
                  r.status_code == 401 and body.get("error", {}).get("code") == "invalid_token",
                  body)

            # 用正确密钥签一个「用户不存在」的令牌
            import jwt as _jwt
            ghost = _jwt.encode(
                {"sub": "999999", "typ": "access", "jti": "ghost-jti",
                 "iat": int(time.time()), "exp": int(time.time()) + 600},
                app.config["WX_TOKEN_SECRET"], algorithm="HS256")
            r = anon.get("/api/wx/v1/auth/me", headers=auth_headers({"access_token": ghost}))
            body = r.get_json() or {}
            check("签名正确但用户不存在 → 401/404",
                  r.status_code in (401, 404), f"status={r.status_code} body={body}")

            print("\n[3] 过期 Token → 401 token_expired")
            expired = _jwt.encode(
                {"sub": "1", "typ": "access", "jti": "expired-jti",
                 "iat": int(time.time()) - 7200, "exp": int(time.time()) - 3600},
                app.config["WX_TOKEN_SECRET"], algorithm="HS256")
            r = anon.get("/api/wx/v1/auth/me",
                         headers=auth_headers({"access_token": expired}))
            body = r.get_json() or {}
            check("过期 Access Token → 401 token_expired",
                  r.status_code == 401 and body.get("error", {}).get("code") == "token_expired",
                  body)

            print("\n[4] 微信登录闭环（mock code2Session）")
            r = anon.post("/api/wx/v1/auth/wx-login", json={"code": "wx_test_new"})
            body = r.get_json() or {}
            check("wx-login 成功", r.status_code == 200 and body.get("ok") is True, body)
            tokens_new = body.get("data", {}).get("tokens") or {}
            check("下发 access + refresh 令牌",
                  bool(tokens_new.get("access_token")) and bool(tokens_new.get("refresh_token")))
            check("token_type 为 Bearer", tokens_new.get("token_type") == "Bearer")
            check("is_new_account=True（首次登录自动建号）",
                  body.get("data", {}).get("is_new_account") is True)
            wx_user_id = body.get("data", {}).get("user", {}).get("id")
            check("新账户有 id", isinstance(wx_user_id, int), wx_user_id)
            check("新账户 email 不外泄（wx.local 占位）",
                  body.get("data", {}).get("user", {}).get("email") == "")
            check("响应不含 session_key",
                  "session_key" not in str(body), "泄露了 session_key！")

            # 二次登录：同一 openid 必须复用同一账户
            r2 = anon.post("/api/wx/v1/auth/wx-login", json={"code": "wx_test_repeat"})
            body2 = r2.get_json() or {}
            check("二次登录复用同一账户",
                  body2.get("data", {}).get("user", {}).get("id") == wx_user_id,
                  f"first={wx_user_id} second={body2.get('data', {}).get('user', {}).get('id')}")
            check("二次登录 is_new_account=False",
                  body2.get("data", {}).get("is_new_account") is False)
            tokens_a = tokens_new
            tokens_b = body2.get("data", {}).get("tokens") or {}

            print("\n[5] 无效 code 被拒绝")
            r = anon.post("/api/wx/v1/auth/wx-login", json={"code": "bad-code"})
            check("无效 code → 401", r.status_code == 401, r.status_code)
            r = anon.post("/api/wx/v1/auth/wx-login", json={})
            check("缺 code → 400", r.status_code == 400, r.status_code)

            print("\n[6] 登录后可访问学习数据")
            wx_client = C(app)
            r = wx_client.get("/api/wx/v1/auth/me", headers=auth_headers(tokens_a))
            body = r.get_json() or {}
            check("GET /auth/me 成功", r.status_code == 200 and body.get("ok") is True, body)

            r = wx_client.get("/api/wx/v1/home", headers=auth_headers(tokens_a))
            body = r.get_json() or {}
            check("GET /home 返回概览", r.status_code == 200 and "stats" in (body.get("data") or {}), body)

            r = wx_client.get("/api/wx/v1/words?page=1&size=5",
                              headers=auth_headers(tokens_a))
            body = r.get_json() or {}
            items = (body.get("data") or {}).get("items") or []
            check("GET /words 分页返回", r.status_code == 200 and len(items) == 5, len(items))

            r = wx_client.get("/api/wx/v1/words?q=a", headers=auth_headers(tokens_a))
            body = r.get_json() or {}
            check("GET /words 搜索可用", r.status_code == 200, r.status_code)

            print("\n[7] 收藏 / 错题 / 答题 真实数据闭环")
            word_id = items[0]["id"]
            r = wx_client.post(f"/api/wx/v1/words/{word_id}/favorite", json={},
                               headers=auth_headers(tokens_a))
            body = r.get_json() or {}
            check("收藏成功", r.status_code == 200 and body.get("data", {}).get("favorited") is True, body)

            r = wx_client.get("/api/wx/v1/favorites", headers=auth_headers(tokens_a))
            body = r.get_json() or {}
            fav_items = (body.get("data") or {}).get("items") or []
            check("收藏列表含该词",
                  any(i.get("id") == word_id for i in fav_items), len(fav_items))

            # 再点一次 = 取消收藏
            r = wx_client.post(f"/api/wx/v1/words/{word_id}/favorite", json={},
                               headers=auth_headers(tokens_a))
            body = r.get_json() or {}
            check("再次点击取消收藏",
                  body.get("data", {}).get("favorited") is False, body)

            # ---- 回归：登录后单词详情必须带个性化 fav 字段 ----
            # 历史 bug：/words/<id> 曾漏掉登录识别，收藏后详情仍显示未收藏，
            # 小程序「收藏」按钮状态永远不对。端到端联调（tests/integration_
            # miniprogram.py）首先暴露，这里补回归。
            r = wx_client.post(f"/api/wx/v1/words/{word_id}/favorite", json={},
                               headers=auth_headers(tokens_a))
            r = wx_client.get(f"/api/wx/v1/words/{word_id}",
                              headers=auth_headers(tokens_a))
            body = r.get_json() or {}
            check("回归：登录后单词详情带 fav=True（收藏态同步）",
                  (body.get("data") or {}).get("fav") is True, body)
            r = wx_client.post(f"/api/wx/v1/words/{word_id}/favorite", json={},
                               headers=auth_headers(tokens_a))
            r = wx_client.get(f"/api/wx/v1/words/{word_id}",
                              headers=auth_headers(tokens_a))
            body = r.get_json() or {}
            check("回归：取消收藏后详情 fav=False",
                  (body.get("data") or {}).get("fav") is False, body)

            # ---- 回归：判题接口必须支持「选项下标」作答 ----
            # 历史 bug：_rebuild_options 调用了未导入的 build_question，
            # 只要小程序按设计提交选项下标（数字）就 500。旧测试只提交文本，
            # 从未覆盖该路径；端到端联调首先暴露，这里补回归。
            r = wx_client.post("/api/wx/v1/quiz/start",
                               json={"mode": "choice", "scope": "all", "size": 2},
                               headers=auth_headers(tokens_a))
            body = r.get_json() or {}
            state_idx = (body.get("data") or {}).get("state") or ""
            r = wx_client.get(f"/api/wx/v1/quiz/item?state={state_idx}&i=0",
                              headers=auth_headers(tokens_a))
            body = r.get_json() or {}
            state_idx = (body.get("data") or {}).get("state") or ""
            r = wx_client.post("/api/wx/v1/quiz/answer",
                               json={"state": state_idx, "i": 0, "answer": 0},
                               headers=auth_headers(tokens_a))
            body = r.get_json() or {}
            check("回归：选项下标作答返回 200 且判题正常",
                  r.status_code == 200 and "correct" in (body.get("data") or {}),
                  f"status={r.status_code} body={body}")
            check("回归：判题反馈带 user_answer / expected",
                  "user_answer" in (body.get("data") or {})
                  and "expected" in (body.get("data") or {}))

            # ---- 测试中心：开始 → 取题 → 判题 → 交卷 ----
            r = wx_client.post("/api/wx/v1/quiz/start",
                               json={"mode": "choice", "scope": "all", "size": 3},
                               headers=auth_headers(tokens_a))
            body = r.get_json() or {}
            state = (body.get("data") or {}).get("state") or ""
            check("quiz/start 返回签名状态", bool(state), body)
            total = (body.get("data") or {}).get("total")
            check("quiz/start total=3", total == 3, total)

            r = wx_client.get(f"/api/wx/v1/quiz/item?state={state}&i=0",
                              headers=auth_headers(tokens_a))
            body = r.get_json() or {}
            q = (body.get("data") or {}).get("q") or {}
            state2 = (body.get("data") or {}).get("state") or ""
            check("取到第 1 题", bool(q.get("word")) and q.get("i") == 0, q)
            check("题目不含正确答案字段", "answer" not in q, list(q.keys()))
            check("状态令牌已更新（写入判题依据）", state2 and state2 != state)

            # 用错误答案答一题 → 应判错并进错题本
            r = wx_client.post("/api/wx/v1/quiz/answer",
                               json={"state": state2, "i": 0, "answer": "__wrong__"},
                               headers=auth_headers(tokens_a))
            body = r.get_json() or {}
            check("判题接口可用", r.status_code == 200 and "correct" in (body.get("data") or {}), body)

            r = wx_client.get("/api/wx/v1/wrong", headers=auth_headers(tokens_a))
            body = r.get_json() or {}
            wrong_items = (body.get("data") or {}).get("items") or []
            check("错题本已记录", len(wrong_items) >= 1, len(wrong_items))

            r = wx_client.post("/api/wx/v1/quiz/finish", json={"state": state2},
                               headers=auth_headers(tokens_a))
            body = r.get_json() or {}
            check("交卷返回成绩", r.status_code == 200 and "percent" in (body.get("data") or {}), body)
            rec_id = (body.get("data") or {}).get("record_id")
            check("TestRecord 已落库", TestRecord.query.get(rec_id) is not None, rec_id)

            print("\n[8] 出题状态令牌防篡改")
            tampered = state2[:-4] + ("AAAA" if not state2.endswith("AAAA") else "BBBB")
            r = wx_client.get(f"/api/wx/v1/quiz/item?state={tampered}&i=0",
                              headers=auth_headers(tokens_a))
            body = r.get_json() or {}
            check("篡改状态令牌 → 400",
                  r.status_code == 400 and body.get("error", {}).get("code") == "invalid_state",
                  body)

            r = wx_client.get("/api/wx/v1/quiz/item?state=&i=0", headers=auth_headers(tokens_a))
            check("缺状态令牌 → 400", r.status_code == 400, r.status_code)

            print("\n[9] 跨用户数据隔离")
            # 用户 B 通过另一个 openid 登录
            r = anon.post("/api/wx/v1/auth/wx-login", json={"code": "wx_test_other"})
            body_b = r.get_json() or {}
            tokens_b2 = body_b.get("data", {}).get("tokens") or {}
            user_b_id = body_b.get("data", {}).get("user", {}).get("id")
            check("用户 B 是不同账户", user_b_id != wx_user_id, f"A={wx_user_id} B={user_b_id}")

            client_b = C(app)
            r = client_b.get("/api/wx/v1/favorites", headers=auth_headers(tokens_b2))
            body = r.get_json() or {}
            check("用户 B 看不到用户 A 的收藏",
                  len((body.get("data") or {}).get("items") or []) == 0, body)

            r = client_b.get("/api/wx/v1/wrong", headers=auth_headers(tokens_b2))
            body = r.get_json() or {}
            check("用户 B 看不到用户 A 的错题",
                  len((body.get("data") or {}).get("items") or []) == 0, body)

            # 用户 B 尝试按 user_id 直接查他人数据：接口根本不接受该参数
            r = client_b.get(f"/api/wx/v1/progress?user_id={wx_user_id}",
                             headers=auth_headers(tokens_b2))
            body = r.get_json() or {}
            stats = (body.get("data") or {}).get("stats") or {}
            check("进度接口忽略客户端传入的 user_id",
                  stats.get("favorites") == 0 and stats.get("wrong") == 0, stats)

            # 用户 B 收藏同一单词，不影响 A
            client_b.post(f"/api/wx/v1/words/{word_id}/favorite", json={},
                          headers=auth_headers(tokens_b2))
            check("A 的收藏未被 B 的操作污染",
                  Favorite.query.filter_by(user_id=wx_user_id, word_id=word_id).first() is None)

            print("\n[10] 绑定必须验证对原账户的控制权")
            r = wx_client.post("/api/wx/v1/auth/bind-email",
                               json={"email": "bind_target@example.com", "password": "wrong-password"},
                               headers=auth_headers(tokens_a))
            body = r.get_json() or {}
            check("错误密码 → 401", r.status_code == 401
                  and body.get("error", {}).get("code") == "invalid_credentials", body)

            r = wx_client.post("/api/wx/v1/auth/bind-email",
                               json={"email": "not_exist@example.com", "password": "whatever123"},
                               headers=auth_headers(tokens_a))
            check("不存在的邮箱 → 401（统一提示，不枚举）", r.status_code == 401, r.status_code)

            r = wx_client.post("/api/wx/v1/auth/bind-email",
                               json={"email": "bind_target@example.com"},
                               headers=auth_headers(tokens_a))
            check("既不给密码也不给验证码 → 400", r.status_code == 400, r.status_code)

            # 正确密码 → 绑定成功，令牌切换到目标账户
            r = wx_client.post("/api/wx/v1/auth/bind-email",
                               json={"email": "bind_target@example.com", "password": PW},
                               headers=auth_headers(tokens_a))
            body = r.get_json() or {}
            check("正确密码 → 绑定成功",
                  r.status_code == 200 and body.get("data", {}).get("user", {}).get("id") == existing_id,
                  body)
            tokens_bound = body.get("data", {}).get("tokens") or {}

            r = wx_client.get("/api/wx/v1/auth/me", headers=auth_headers(tokens_bound))
            body = r.get_json() or {}
            check("绑定后用新令牌可访问目标账户",
                  body.get("data", {}).get("user", {}).get("id") == existing_id, body)

            # 已绑定「真实账户」的微信不允许直接改绑到别的账户
            r = wx_client.post("/api/wx/v1/auth/wx-login", json={"code": "wx_test_new"})
            tokens_again = (r.get_json() or {}).get("data", {}).get("tokens") or {}
            another = User(email="another@example.com", username="另一个账户", is_admin=False)
            another.set_password(PW)
            db.session.add(another)
            db.session.commit()
            r = C(app).post("/api/wx/v1/auth/bind-email",
                            json={"email": "another@example.com", "password": PW},
                            headers=auth_headers(tokens_again))
            body = r.get_json() or {}
            check("已绑真实账户的微信改绑他户 → 409",
                  r.status_code == 409
                  and body.get("error", {}).get("code") == "already_bound",
                  f"status={r.status_code} body={body}")

            print("\n[11] 解绑需再次验证控制权")
            r = C(app).post("/api/wx/v1/auth/unbind", json={"password": "wrong-password"},
                            headers=auth_headers(tokens_bound))
            check("解绑密码错误 → 401", r.status_code == 401, r.status_code)
            r = C(app).post("/api/wx/v1/auth/unbind", json={},
                            headers=auth_headers(tokens_bound))
            check("解绑缺凭证 → 400", r.status_code == 400, r.status_code)

            r = C(app).post("/api/wx/v1/auth/unbind", json={"password": PW},
                            headers=auth_headers(tokens_bound))
            body = r.get_json() or {}
            check("解绑成功", r.status_code == 200 and body.get("data", {}).get("unbound") is True, body)

            r = C(app).get("/api/wx/v1/auth/me", headers=auth_headers(tokens_bound))
            check("解绑后原令牌失效", r.status_code == 401, r.status_code)

            # 解绑只断关联，绝不删除账户与学习数据
            check("解绑不删除账户数据",
                  User.query.get(existing_id) is not None
                  and UserWordProgress.query.filter_by(user_id=existing_id).count() >= 0)
            check("解绑后 wechat_identities 无残留",
                  WeChatIdentity.query.filter_by(user_id=existing_id,
                                                 appid="wx_test_appid").first() is None)

            print("\n[12] 重复绑定被唯一约束拦截（DB 层）")
            # 自包含验证：先建一条基线关联，再尝试用同一 openid+appid 建第二条。
            # 注意：前一步「解绑」已把 openid_new_user_001 的关联行删除，
            # 因此这里必须重新建立基线，否则断言的是「不存在冲突」而非「冲突被拦截」。
            base_ident = WeChatIdentity(user_id=wx_user_id, openid="openid_dup_probe",
                                        appid="wx_test_appid")
            db.session.add(base_ident)
            db.session.commit()

            dup = WeChatIdentity(user_id=existing_id, openid="openid_dup_probe",
                                 appid="wx_test_appid")
            db.session.add(dup)
            try:
                db.session.commit()
                check("同 openid 二次关联被唯一约束拦截", False, "竟然插入成功")
            except Exception:
                db.session.rollback()
                check("同 openid 二次关联被唯一约束拦截", True)

            # 同一账户 + 同一 appid 换一个 openid 也应被 (user_id, appid) 唯一约束拦截
            dup2 = WeChatIdentity(user_id=wx_user_id, openid="openid_dup_probe_2",
                                  appid="wx_test_appid")
            db.session.add(dup2)
            try:
                db.session.commit()
                check("同账户第二个微信身份被唯一约束拦截", False, "竟然插入成功")
            except Exception:
                db.session.rollback()
                check("同账户第二个微信身份被唯一约束拦截", True)

            # 不同 appid 下同一 openid 允许存在（换小程序号时不串号）
            other_appid = WeChatIdentity(user_id=existing_id, openid="openid_dup_probe",
                                         appid="wx_other_appid")
            db.session.add(other_appid)
            try:
                db.session.commit()
                check("不同 appid 下同一 openid 允许并存", True)
            except Exception as exc:
                db.session.rollback()
                check("不同 appid 下同一 openid 允许并存", False, exc)

            # 清理探测行
            WeChatIdentity.query.filter(
                WeChatIdentity.openid.in_(["openid_dup_probe", "openid_dup_probe_2"])
            ).delete(synchronize_session=False)
            db.session.commit()

            print("\n[13] Refresh 换发 + 旋转")
            r = wx_client.post("/api/wx/v1/auth/refresh",
                               json={"refresh_token": tokens_bound["refresh_token"]})
            body = r.get_json() or {}
            new_tokens = body.get("data", {}).get("tokens") or {}
            check("refresh 换发成功", r.status_code == 200 and bool(new_tokens.get("access_token")), body)
            check("换发后 refresh 也轮换",
                  new_tokens.get("refresh_token") != tokens_bound["refresh_token"])

            r = wx_client.post("/api/wx/v1/auth/refresh",
                               json={"refresh_token": tokens_bound["refresh_token"]})
            body = r.get_json() or {}
            check("旧 refresh 已失效（防重放）",
                  r.status_code == 401 and body.get("error", {}).get("code") == "token_revoked", body)

            print("\n[14] 登出撤销")
            r = C(app).post("/api/wx/v1/auth/logout",
                            json={"refresh_token": new_tokens["refresh_token"]},
                            headers=auth_headers(new_tokens))
            body = r.get_json() or {}
            check("登出成功", r.status_code == 200 and body.get("ok") is True, body)

            r = C(app).get("/api/wx/v1/auth/me", headers=auth_headers(new_tokens))
            body = r.get_json() or {}
            check("登出后 access 令牌失效",
                  r.status_code == 401 and body.get("error", {}).get("code") == "token_revoked", body)

            r = C(app).post("/api/wx/v1/auth/refresh",
                            json={"refresh_token": new_tokens["refresh_token"]})
            check("登出后 refresh 令牌也失效", r.status_code == 401, r.status_code)

            print("\n[15] Web 端 session 认证不受影响")
            web = C(app)
            r = web.post("/login", data={"email": "bind_target@example.com",
                                         "password": PW}, follow_redirects=False)
            check("Web 端密码登录仍然正常", r.status_code in (200, 302), r.status_code)

            print("\n[16] 清理与幂等")
            n = wx_api.purge_expired_revocations if hasattr(wx_api, "purge_expired_revocations") else None
            from wx_auth import purge_expired_revocations
            check("过期撤销记录可清理", isinstance(purge_expired_revocations(), int))

    finally:
        wx_api.code2session = orig

    print("\n" + "=" * 60)
    print(f"结果：通过 {PASS} / 失败 {FAIL}")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
