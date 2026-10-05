"""V3.0 REST API v1 测试（Web / 小程序 / Android / macOS 共用接口）。

验证统一响应结构 ``{ok, data, error}``，以及：

* 元信息：/meta/skills（6 项）、/meta/games（4 项）、/health（version 3.0.0）
* 单词：/words 搜索、/words/<id>、/words/random
* 用户：/me/overview、/me/progress（需登录）、/me/goal（改目标）
* 游戏：/games/<key>/start、/games/<key>/submit、/games/stats（需登录）
* 播客：/podcast/channels、/podcast/episodes（架构预留，返回空列表而非 404）
* 鉴权：匿名访问受保护接口应 401；登录后可正常调用

所有判分逻辑与 routes/games 共用 game_service，因此这里用 view 反查正确选项，
验证「开局存 session、提交用保存状态判分」在 API 层同样成立。
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
from models import GameRecord, SKILLS, GAMES  # noqa: E402
from game_helpers import view_answers  # noqa: E402

# 复用 test_flow 的脚手架（build / 登录隔离）
from test_flow import build, C, join, signin  # noqa: E402

PASS = FAIL = 0


def check(name, cond, extra=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("  PASS  " + name)
    else:
        FAIL += 1
        print("  FAIL  " + name + ("  << " + str(extra) if extra else ""))


def _payload(key, answers, elapsed_sec=5):
    if key == "word_match":
        return {"pairs": answers}
    if key == "speed_quiz":
        return {"answers": answers, "elapsed_sec": elapsed_sec}
    return {"answers": answers}


def main():
    app = build(False)
    with app.app_context():
        print("\n[1] 元信息 / 健康")
        anon = C(app)
        h = anon.get("/api/v1/health").get_json()
        check("health ok", bool(h and h.get("ok")))
        check("health version=3.0.0", (h or {}).get("data", {}).get("version") == "3.0.0",
              (h or {}).get("data"))

        sk = anon.get("/api/v1/meta/skills").get_json()
        check("meta/skills 返回 6 项技能", (sk or {}).get("data", {}).get("items", []) and
              len((sk or {}).get("data", {}).get("items", [])) == len(SKILLS), sk)
        gm = anon.get("/api/v1/meta/games").get_json()
        check("meta/games 返回 4 款游戏", (gm or {}).get("data", {}).get("items", []) and
              len((gm or {}).get("data", {}).get("items", [])) == len(GAMES), gm)

        print("\n[2] 单词查询")
        w = anon.get("/api/v1/words?q=ability").get_json()
        check("words 搜索 ok", bool(w and w.get("ok") and "items" in w["data"]), w)
        w1 = anon.get("/api/v1/words/1").get_json()
        check("words/<id> ok", bool(w1 and w1.get("ok") and w1["data"].get("word")), w1)
        wr = anon.get("/api/v1/words/random?size=3").get_json()
        check("words/random ok", bool(wr and wr.get("ok") and len(wr["data"]["items"]) > 0), wr)

        print("\n[3] 用户接口鉴权")
        ov = anon.get("/api/v1/me/overview")
        check("me/overview 匿名 401", ov.status_code == 401, ov.status_code)
        ovj = ov.get_json()
        check("401 带 error.code=unauthorized", (ovj or {}).get("error", {}).get("code") == "unauthorized", ovj)

        print("\n[4] 登录后用户接口")
        c = C(app)
        join(c, "apiuser@demo.com", "接口玩家")
        signin(c, "apiuser@demo.com")
        ov2 = c.get("/api/v1/me/overview").get_json()
        check("me/overview 登录后 ok", bool(ov2 and ov2.get("ok") and "level" in ov2["data"]), ov2)
        prog = c.get("/api/v1/me/progress").get_json()
        check("me/progress ok", bool(prog and prog.get("ok")), prog)
        goal = c.post("/api/v1/me/goal", json={"goal": 30}).get_json()
        check("me/goal 设置 ok", bool(goal and goal.get("ok") and goal["data"].get("goal") == 30), goal)

        print("\n[5] 游戏 API（start/submit/stats）")
        for key in [g["key"] for g in GAMES]:
            cg = C(app)
            signin(cg, "apiuser@demo.com")
            st = cg.post(f"/api/v1/games/{key}/start", json={"seed": 7}).get_json()
            check(f"[{key}] start ok", bool(st and st.get("ok")), st)
            q = (st or {}).get("data") or {}
            ans = view_answers(key, q)
            check(f"[{key}] view 含题目", len(ans) > 0, q)
            before = GameRecord.query.count()
            sb = cg.post(f"/api/v1/games/{key}/submit", json=_payload(key, ans, 4)).get_json()
            check(f"[{key}] submit ok", bool(sb and sb.get("ok")), sb)
            res = (sb or {}).get("data") or {}
            check(f"[{key}] 全对 correct==total",
                  res.get("correct") == res.get("total") == len(ans), res)
            check(f"[{key}] 落库 +1", GameRecord.query.count() == before + 1)

            # 未开局提交 -> 409
            cg2 = C(app)
            signin(cg2, "apiuser@demo.com")
            lost = cg2.post(f"/api/v1/games/{key}/submit", json=_payload(key, ans, 4))
            check(f"[{key}] 未开局提交 409", lost.status_code == 409, lost.status_code)

        stats = c.get("/api/v1/games/stats").get_json()
        items = (stats or {}).get("data", {}).get("items", [])
        check("games/stats 返回 4 项", len(items) == len(GAMES), stats)

        print("\n[6] 播客（架构预留）")
        ch = anon.get("/api/v1/podcast/channels").get_json()
        check("podcast/channels ok（空列表）", bool(ch and ch.get("ok") and "items" in ch["data"]), ch)
        ep = anon.get("/api/v1/podcast/episodes").get_json()
        check("podcast/episodes ok（空列表）", bool(ep and ep.get("ok") and "items" in ep["data"]), ep)

        print("\n" + "=" * 62)
        print(f"结果：通过 {PASS} / 失败 {FAIL}")
        print("=" * 62)
        sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    main()
