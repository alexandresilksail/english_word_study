"""小程序端到端联调：起真实 HTTP 服务，按页面实际调用顺序打全链路。

与 tests/test_wx_api.py 的区别：那个用 Flask test client 测**服务端行为**；
本脚本起一个**真实的 dev server**（后台线程），用 urllib 发**真实 HTTP 请求**，
严格按小程序页面（utils/api.js 的约定）的调用顺序与参数形态逐个接口验证，
并检查每个页面真实读取的字段都存在且类型正确。

覆盖页面：
  login → home → words → word-detail → quiz → quiz-result
       → favorites → wrong → progress → profile

运行：python tests/integration_miniprogram.py
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request

BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)
sys.path.insert(0, os.path.dirname(__file__))

from config import TestingConfig  # noqa: E402
from app import create_app  # noqa: E402
from extensions import db  # noqa: E402
from models import Word  # noqa: E402

import wx_api  # noqa: E402

PORT = 5099
BASE = f"http://127.0.0.1:{PORT}"
PASS = FAIL = 0


def check(name, cond, extra=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("  PASS  " + name)
    else:
        FAIL += 1
        print("  FAIL  " + name + ("  << " + str(extra) if extra else ""))


class _FakeWx:
    """模拟微信 code2Session（不发真实网络请求）。"""

    def __call__(self, code, timeout=8.0):
        if code == "mp_e2e_code":
            return {"ok": True, "openid": "mp_e2e_openid_001", "unionid": ""}
        return {"ok": False, "errcode": 40029, "errmsg": "invalid js_code"}


def http(method, path, body=None, token=None, timeout=15):
    """真实 HTTP 请求（模拟 wx.request）。"""
    data = None
    headers = {"Content-Type": "application/json"}
    if body is not None:
        data = json.dumps(body).encode("utf-8")
    if token:
        headers["Authorization"] = "Bearer " + token
    req = urllib.request.Request(BASE + path, data=data, headers=headers,
                                 method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as res:
            return res.status, json.loads(res.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode("utf-8"))


def build_app():
    tmpdir = tempfile.mkdtemp(prefix="mp_e2e_")
    db_path = os.path.join(tmpdir, "integration.db").replace("\\", "/")

    class Cfg(TestingConfig):
        DATABASE_URL = f"sqlite:///{db_path}"

    app = create_app(Cfg)
    app.config.update(
        WX_TOKEN_SECRET="mp-e2e-token-secret",
        WX_QUIZ_STATE_SECRET="mp-e2e-quiz-secret",
        WX_APPID="wx_mp_e2e_appid",
        WX_AUTO_REGISTER=True,
    )
    with app.app_context():
        db.create_all()
        from seeds.seed_words import seed_from_json
        seed_from_json(app.config["WORDS_JSON"])
    return app


def seed_for_user(app, user_id):
    """给指定用户造学习数据（收藏 / 错题 / 答题记录），保证各页有真实内容。

    注意：record_answer(correct=False) 本身就会写入错题本，
    不要额外手工插 WrongAnswer，否则撞唯一约束。
    """
    with app.app_context():
        from models import Favorite
        from services import record_answer, touch_word
        words = Word.query.limit(6).all()
        for i, w in enumerate(words[:4]):
            touch_word(user_id, w.id, "view")
            # 一半对一半错：错的自动进错题本
            record_answer(user_id, w.id, correct=(i % 2 == 0), mode="choice")
        db.session.add(Favorite(user_id=user_id, word_id=words[0].id))
        db.session.add(Favorite(user_id=user_id, word_id=words[2].id))
        db.session.commit()


def main():
    wx_api.code2session = _FakeWx()
    app = build_app()

    # ---- 起真实 HTTP 服务（后台线程） ----
    server = threading.Thread(
        target=lambda: app.run(host="127.0.0.1", port=PORT, threaded=True,
                               debug=False, use_reloader=False),
        daemon=True)
    server.start()
    for _ in range(50):
        try:
            urllib.request.urlopen(BASE + "/api/wx/v1/health", timeout=1)
            break
        except Exception:
            time.sleep(0.2)
    print("== 服务已启动:", BASE)

    try:
        # ================= login 页 =================
        print("\n[login] 微信一键登录（code → 令牌）")
        st, body = http("POST", "/api/wx/v1/auth/wx-login",
                        {"code": "mp_e2e_code"})
        check("wx-login 200", st == 200, body)
        check("下发 access/refresh 双令牌",
              bool(body.get("data", {}).get("tokens", {}).get("access_token"))
              and bool(body["data"]["tokens"].get("refresh_token")))
        check("响应不含 session_key",
              "session_key" not in json.dumps(body))
        token = body["data"]["tokens"]["access_token"]
        refresh = body["data"]["tokens"]["refresh_token"]
        check("返回 user 字段（profile 页需要）",
              all(k in body["data"]["user"] for k in
                  ("username", "email", "email_bound", "level", "xp",
                   "daily_goal", "streak_days")))

        # 给这个新账户造学习数据（收藏 / 错题 / 答题），让后续页面有真实内容
        seed_for_user(app, body["data"]["user"]["id"])

        # ================= profile 页 =================
        print("\n[profile] GET /auth/me")
        st, body = http("GET", "/api/wx/v1/auth/me", token=token)
        check("auth/me 200", st == 200, body)
        user = body["data"]["user"]
        check("user.id/username/daily_goal 存在",
              bool(user.get("id")) and "daily_goal" in user)

        print("\n[profile] POST /me/goal 设置每日目标")
        st, body = http("POST", "/api/wx/v1/me/goal", {"goal": 35}, token=token)
        check("设置目标 200 且回读生效",
              st == 200 and body["data"]["goal"] == 35, body)

        # ================= home 页 =================
        print("\n[home] GET /home")
        st, body = http("GET", "/api/wx/v1/home", token=token)
        check("home 200", st == 200, body)
        d = body["data"]
        check("首页字段齐全（level/streak_days/daily_goal/today_done/stats）",
              all(k in d for k in ("level", "streak_days", "daily_goal",
                                   "today_done", "stats", "wrong_count",
                                   "favorite_count")), list(d.keys()))
        check("stats 含 learned/mastered/accuracy/total_words",
              all(k in d["stats"] for k in
                  ("learned", "mastered", "accuracy", "total_words")))

        # ================= words 页 =================
        print("\n[words] GET /words?page=1&size=20")
        st, body = http("GET", "/api/wx/v1/words?page=1&size=20", token=token)
        check("words 200 且分页结构完整",
              st == 200 and all(k in body["data"] for k in
                                ("items", "page", "pages", "total")))
        wid = body["data"]["items"][0]["id"]
        check("列表项含 word/phonetic_uk/meaning_cn/status/audio_url",
              all(k in body["data"]["items"][0] for k in
                  ("word", "phonetic_uk", "meaning_cn", "status", "audio_url")))

        print("\n[words] 搜索 q=ab")
        st, body = http("GET", "/api/wx/v1/words?q=ab&page=1&size=20",
                        token=token)
        check("搜索返回 200", st == 200)

        # ================= word-detail 页 =================
        print("\n[word-detail] GET /words/<id>")
        st, body = http("GET", f"/api/wx/v1/words/{wid}", token=token)
        check("单词详情 200 且字段齐全",
              st == 200 and all(k in body["data"] for k in
                                ("word", "phonetic_uk", "meaning_cn",
                                 "example_en", "example_cn", "audio_url",
                                 "fav", "status")), list(body["data"].keys()))

        print("\n[word-detail] POST /words/<id>/favorite 收藏/取消")
        st, body = http("POST", f"/api/wx/v1/words/{wid}/favorite",
                        token=token)
        check("收藏成功", st == 200 and body["data"]["favorited"] is True)
        st, body = http("POST", f"/api/wx/v1/words/{wid}/favorite",
                        token=token)
        check("再次调用取消收藏", st == 200 and body["data"]["favorited"] is False)

        # ================= quiz 页（四种模式全流程） =================
        for mode in ("choice", "zh_en", "listen", "spell"):
            print(f"\n[quiz] 模式 {mode} 全流程")
            st, body = http("POST", "/api/wx/v1/quiz/start",
                            {"mode": mode, "scope": "all", "size": 5},
                            token=token)
            check(f"{mode}: start 200 且下发 state",
                  st == 200 and bool(body["data"].get("state")))
            state = body["data"]["state"]
            total = body["data"]["total"]
            check(f"{mode}: total=5", total == 5)

            st, body = http("GET", f"/api/wx/v1/quiz/item?state={state}&i=0",
                            token=token)
            check(f"{mode}: item 200", st == 200, body)
            q = body["data"]["q"]
            state = body["data"]["state"]   # 必须覆盖本地 state
            check(f"{mode}: 题目含 i/total，且不含 answer",
                  q.get("i") == 0 and q.get("total") == 5 and "answer" not in q)
            if mode == "listen":
                check("listen: item 带 audio_url（可播放）",
                      bool(q.get("audio_url")), q)
            if mode == "spell":
                check("spell: 含 first/length 提示",
                      "first" in q and "length" in q)
            if mode != "spell":
                check(f"{mode}: 4 个选项", len(q.get("options", [])) == 4)

            answer = 0 if mode != "spell" else q.get("word", "")
            st, body = http("POST", "/api/wx/v1/quiz/answer",
                            {"state": state, "i": 0, "answer": answer},
                            token=token)
            check(f"{mode}: answer 200 且返回判题反馈",
                  st == 200 and "correct" in body["data"]
                  and "expected" in body["data"], body)
            check(f"{mode}: 反馈含 word/meaning/example（页面展示用）",
                  all(k in body["data"] for k in
                      ("word", "meaning", "example_en", "phonetic")))
            state = body["data"]["state"]   # 再次覆盖

            # 剩余题目答完
            for i in range(1, total):
                st, item_resp = http(
                    "GET", f"/api/wx/v1/quiz/item?state={state}&i={i}",
                    token=token)
                if st != 200:
                    check(f"{mode}: item[{i}] 200", False, item_resp)
                    break
                state = item_resp["data"]["state"]
                # spell 模式直接提交正确拼写（q.word 出题时已下发）
                answer = 0 if mode != "spell" else \
                    item_resp["data"]["q"].get("word", "")
                st, ans_resp = http("POST", "/api/wx/v1/quiz/answer",
                                    {"state": state, "i": i, "answer": answer},
                                    token=token)
                if st != 200:
                    check(f"{mode}: answer[{i}] 200", False, ans_resp)
                    break
                state = ans_resp["data"]["state"]
            else:
                check(f"{mode}: 全部 {total} 题作答成功", True)

            st, body = http("POST", "/api/wx/v1/quiz/finish",
                            {"state": state}, token=token)
            check(f"{mode}: finish 200 且成绩字段齐全",
                  st == 200 and all(k in body["data"] for k in
                                    ("total", "score", "percent", "duration",
                                     "comment", "record_id")), body)
            check(f"{mode}: 成绩写入（score<=total）",
                  body["data"]["score"] <= body["data"]["total"])

        # ================= favorites 页 =================
        print("\n[favorites] GET /favorites?page=1&size=20")
        st, body = http("GET", "/api/wx/v1/favorites?page=1&size=20",
                        token=token)
        check("favorites 200 且分页结构完整",
              st == 200 and all(k in body["data"] for k in
                                ("items", "page", "pages", "total")))
        if body["data"]["items"]:
            check("收藏项含 favorited_at",
                  "favorited_at" in body["data"]["items"][0])

        # ================= wrong 页 =================
        print("\n[wrong] GET /wrong?page=1&size=20")
        st, body = http("GET", "/api/wx/v1/wrong?page=1&size=20", token=token)
        check("wrong 200 且分页结构完整",
              st == 200 and all(k in body["data"] for k in
                                ("items", "page", "pages", "total")))
        if body["data"]["items"]:
            item = body["data"]["items"][0]
            check("错题项含 wrong_count/last_wrong_at",
                  "wrong_count" in item and "last_wrong_at" in item)
            st, body = http("POST",
                            f"/api/wx/v1/wrong/{item['id']}/remove",
                            token=token)
            check("移出错题 200", st == 200)

        # ================= progress 页 =================
        print("\n[progress] GET /progress")
        st, body = http("GET", "/api/wx/v1/progress", token=token)
        check("progress 200", st == 200, body)
        d = body["data"]
        check("进度字段齐全（stats/trend/streak/gamification/badges/skills）",
              all(k in d for k in ("stats", "trend", "streak",
                                   "gamification", "badges", "skills")))
        check("trend 为 14 天", len(d["trend"]) == 14)
        check("trend 项含 day/label/total",
              all(k in d["trend"][0] for k in ("day", "label", "total")))
        check("gamification 含 level/xp/level_percent/xp_to_next",
              all(k in d["gamification"] for k in
                  ("level", "xp", "level_percent", "xp_to_next")))
        check("badges 含 name/unlocked",
              all(k in d["badges"][0] for k in ("name", "unlocked")))
        check("skills 含 skill/xp（无记录时为空列表，页面已做空态处理）",
              all(k in s for k in ("skill", "xp") for s in d["skills"]))

        # ================= token 刷新 / 登出 =================
        print("\n[auth] refresh 旋转")
        st, body = http("POST", "/api/wx/v1/auth/refresh",
                        {"refresh_token": refresh})
        check("refresh 200 且换发新令牌", st == 200 and body["data"]["tokens"])
        new_refresh = body["data"]["tokens"]["refresh_token"]
        st, body = http("POST", "/api/wx/v1/auth/refresh",
                        {"refresh_token": refresh})
        check("旧 refresh 已失效（旋转生效）", st == 401, body)

        print("\n[auth] logout 撤销")
        st, me = http("GET", "/api/wx/v1/auth/me", token=token)
        check("原 access 仍有效（未过期）", st == 200)
        st, body = http("POST", "/api/wx/v1/auth/logout",
                        {"refresh_token": new_refresh}, token=token)
        check("logout 200", st == 200, body)
        st, body = http("GET", "/api/wx/v1/auth/me", token=token)
        check("登出后 access 失效（已撤销）", st == 401, body)

        # ================= Web 端不受影响 =================
        print("\n[web] 原 Web 登录页仍可访问")
        req = urllib.request.Request(BASE + "/login", method="GET")
        try:
            with urllib.request.urlopen(req, timeout=10) as res:
                web_status = res.status
        except urllib.error.HTTPError as e:
            web_status = e.code     # 302 等重定向在 urlopen 里会成异常
        check("Web /login 可访问（200/302）", web_status in (200, 302), web_status)

    finally:
        print(f"\n===== 联调结果: {PASS} 通过 / {FAIL} 失败 =====")
        if FAIL:
            sys.exit(1)


if __name__ == "__main__":
    main()
