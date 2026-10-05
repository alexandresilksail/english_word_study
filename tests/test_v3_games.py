"""V3.0 小游戏端到端测试（Word Match / Speed Quiz / Listening / Word Builder）。

覆盖两层：

A. **纯 service 层（不依赖 HTTP）**：直接调用 game_service 的 start/submit，
   用保存的 state 推导正确答案，验证「判分用出题时保存的状态、绝不重新随机」，
   以及全对 / 全错两种极端下的计分正确。

B. **HTTP 端到端（真实路由）**：登录后 POST /games/api/<key>/start 拿题目，
   用 view 反查正确选项并提交，验证路由把 state 存进 session、record_game
   真正落库、以及「未开局直接提交」应返回 409。

判分原则与已修复的 Quiz 判分完全一致：答案只存在于服务端 session/state，
前端永远拿不到，任何重随机都会让「全对」变「全错」从而被本测试抓到。
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
from models import GameRecord, User  # noqa: E402
from game_service import (GAME_REGISTRY, start_game, submit_game)  # noqa: E402
from game_helpers import state_answers, view_answers  # noqa: E402

PASS = FAIL = 0
PW = "Study123456"

ALL_KEYS = list(GAME_REGISTRY.keys())


def check(name, cond, extra=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("  PASS  " + name)
    else:
        FAIL += 1
        print("  FAIL  " + name + ("  << " + str(extra) if extra else ""))


def build(csrf_enabled: bool = False):
    app = create_app(TestingConfig)
    app.config["WTF_CSRF_ENABLED"] = csrf_enabled
    app.config["SERVER_NAME"] = None
    with app.app_context():
        db.create_all()
        from seeds.seed_words import seed_from_json
        seed_from_json(app.config["WORDS_JSON"])
    return app


class CleanWrapper:
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


def join(c, email, username, pw=PW):
    return c.post("/register", data={"email": email, "username": username,
                                     "password": pw, "confirm": pw})


def signin(c, email, pw=PW):
    return c.post("/login", data={"email": email, "password": pw})


def _submit_payload(key, answers, elapsed_sec=10):
    if key == "word_match":
        return {"pairs": answers}
    if key == "speed_quiz":
        return {"answers": answers, "elapsed_sec": elapsed_sec}
    return {"answers": answers}


def main():
    app = build(False)
    with app.app_context():
        print("\n[A] service 层：判分用保存状态，不重新随机")
        for key in ALL_KEYS:
            res = start_game(key, seed=12345)
            check(f"[{key}] start 成功", bool(res.get("ok")), res)
            state = res["state"]
            view = res["view"]
            # 全对
            ans = state_answers(key, state)
            r_ok = submit_game(key, state, _submit_payload(key, ans, 0))
            check(f"[{key}] 全对 -> correct==total",
                  bool(r_ok.get("ok")) and r_ok["correct"] == r_ok["total"] == len(ans),
                  r_ok)
            check(f"[{key}] 全对 -> accuracy==100",
                  r_ok.get("accuracy") == 100, r_ok)
            check(f"[{key}] 全对 -> score>0", (r_ok.get("score") or 0) > 0, r_ok)
            # 全错：把每个选项 +1 错位
            wrong = []
            for a in ans:
                if key == "word_match":
                    wrong.append([a[0], (a[1] + 1) % len(view["right"])])
                elif key == "word_builder":
                    wrong.append([a[0], "zzzzz"])
                else:
                    n = len(view["questions"][0]["options"])
                    wrong.append([a[0], (a[1] + 1) % n])
            r_bad = submit_game(key, state, _submit_payload(key, wrong, 0))
            check(f"[{key}] 全错 -> correct==0",
                  bool(r_bad.get("ok")) and r_bad["correct"] == 0, r_bad)
            # 状态丢失应判失败
            r_lost = submit_game(key, {}, _submit_payload(key, ans, 0))
            check(f"[{key}] 状态丢失 -> 失败", not r_lost.get("ok"), r_lost)

        print("\n[B] HTTP 端到端：真实路由 + session + 落库")
        c = C(app)
        join(c, "gamer@demo.com", "游戏玩家")
        signin(c, "gamer@demo.com")
        for key in ALL_KEYS:
            r = c.post(f"/games/api/{key}/start", json={"seed": 999})
            st = r.get_json()
            check(f"[{key}] 开局 200/ok", r.status_code == 200 and bool(st and st.get("ok")),
                  (r.status_code, st))
            q = (st or {}).get("q") or {}
            ans = view_answers(key, q)
            check(f"[{key}] view 含完整题目", len(ans) > 0, q)
            before = GameRecord.query.count()
            r2 = c.post(f"/games/api/{key}/submit",
                        json=_submit_payload(key, ans, 5))
            st2 = r2.get_json()
            check(f"[{key}] 提交 200/ok", r2.status_code == 200 and bool(st2 and st2.get("ok")),
                  (r2.status_code, st2))
            res = (st2 or {}).get("result") or {}
            check(f"[{key}] 全对 -> correct==total 落库前一致",
                  res.get("correct") == res.get("total") == len(ans), res)
            check(f"[{key}] GameRecord 落库",
                  GameRecord.query.count() == before + 1)

            # 未开局直接提交 -> 409
            c2 = C(app)
            signin(c2, "gamer@demo.com")
            r3 = c2.post(f"/games/api/{key}/submit",
                         json=_submit_payload(key, ans, 5))
            check(f"[{key}] 未开局提交 -> 409",
                  r3.status_code == 409, r3.status_code)

        # 匿名禁止调用
        anon = C(app)
        r4 = anon.post("/games/api/word_match/start", json={})
        check("匿名开局被拦截", r4.status_code in (302, 401), r4.status_code)

        print("\n" + "=" * 62)
        print(f"结果：通过 {PASS} / 失败 {FAIL}")
        print("=" * 62)
        sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    main()
