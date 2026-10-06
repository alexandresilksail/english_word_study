"""V3.0 页面渲染测试（登录态 / 匿名态 / i18n / 诚实「Coming soon」）。

验证本轮新增与改写的页面都能正确渲染、拿到了模板所需变量、没有把
i18n key 当成字面量（例如把 ``nav.games`` 原样显示出来），以及未上线模块
（AI Tutor / 非词汇技能 / Podcast）严格走「漂亮入口 + 诚实说明」路线，
不伪造内容、不假装有 AI。
"""
from __future__ import annotations

import os
import sys

BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from config import TestingConfig  # noqa: E402
from app import create_app  # noqa: E402
from extensions import db  # noqa: E402

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


def build(csrf_enabled: bool = False):
    app = create_app(TestingConfig)
    app.config["WTF_CSRF_ENABLED"] = csrf_enabled
    app.config["SERVER_NAME"] = None
    with app.app_context():
        db.create_all()
        from seeds.seed_words import seed_from_json
        seed_from_json(app.config["WORDS_JSON"])
        from path_service import ensure_word_meta
        ensure_word_meta()
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


def C(app):
    return CleanWrapper(app)


def join(c, email, username, pw=PW):
    return c.client.post("/register", data={"email": email, "username": username,
                                           "password": pw, "confirm": pw})


def signin(c, email, pw=PW):
    return c.client.post("/login", data={"email": email, "password": pw})


def main():
    app = build(False)
    with app.app_context():
        print("\n[1] 登录态下 V3 页面全部 200")
        c = C(app)
        join(c, "pager@demo.com", "页面玩家")
        signin(c, "pager@demo.com")

        pages = {
            "/dashboard": ["词汇", "听力"],                       # 游戏化 + 技能卡
            "/learn": ["词汇", "听力", "阅读", "语法", "口语", "写作"],
            "/learn/vocabulary": ["今日学习", "单词本", "测试", "错题本", "我的收藏"],
            "/learn/listening": ["Coming soon", "精听播客"],
            "/learn/reading": ["Coming soon", "分级短文"],
            "/learn/grammar": ["Coming soon", "情景语法"],
            "/learn/speaking": ["Coming soon", "跟读打分"],
            "/learn/writing": ["Coming soon", "句型练习"],
            "/games/": ["单词配对", "限时抢答", "听音挑战", "字母拼词"],
            "/games/word_match": ["单词配对", "怎么玩"],
            "/games/speed_quiz": ["限时抢答", "怎么玩"],
            "/games/listening_challenge": ["听音挑战", "怎么玩"],
            "/games/word_builder": ["字母拼词", "怎么玩"],
            "/ai-tutor": ["Coming soon", "规划中", "不假装有 AI"],
            "/podcast/": ["播客", "Podcast", "文稿"],
        }
        for path, needles in pages.items():
            r = c.get(path)
            body = r.get_data(as_text=True)
            ok = r.status_code == 200
            for n in needles:
                if n not in body:
                    ok = False
                    break
            check(f"{path} -> 200 且含关键文案", ok,
                  f"status={r.status_code} missing={[n for n in needles if n not in body]}")

        print("\n[2] i18n 已解析（不出现字面量 key）")
        body = c.get("/learn").get_data(as_text=True)
        check("learn 页不含字面量 nav 键", "nav.games" not in body and "nav.ai_tutor" not in body
              and "skill.vocabulary" not in body, "出现未解析的 i18n key")
        nav_html = c.get("/dashboard").get_data(as_text=True)
        check("dashboard 渲染出游戏/技能导航", ("游戏" in nav_html or "Games" in nav_html),
              "导航无游戏入口")

        print("\n[3] AI Tutor 诚实：有规划，不假装有 AI")
        ai = c.get("/ai-tutor").get_data(as_text=True)
        # 不应出现「真实可用」的聊天输入框（action 指向真实 AI 接口）
        check("AI Tutor 不含伪装可用的聊天表单",
              ("/api/ai/" not in ai) and ("ai-chat" not in ai) and ("gpt" not in ai.lower()),
              "疑似暴露了真实 AI 接口")

        print("\n[4] 匿名访问控制")
        anon = C(app)
        r_pod = anon.get("/podcast/")
        check("播客页匿名可访问", r_pod.status_code == 200, r_pod.status_code)
        r_ai = anon.get("/ai-tutor")
        loc = r_ai.headers.get("Location") or ""
        check("AI Tutor 匿名被拦截跳登录", r_ai.status_code == 302 and "/login" in loc,
              f"{r_ai.status_code} {loc}")
        r_dash = anon.get("/dashboard")
        check("Dashboard 匿名被拦截", r_dash.status_code == 302 and "/login" in
              (r_dash.headers.get("Location") or ""), r_dash.status_code)

        print("\n[5] V5 学习路径 + i18n 切换")
        rp = c.get("/path")
        pbody = rp.get_data(as_text=True)
        check("/path 学习路径页 200", rp.status_code == 200, rp.status_code)
        check("/path 含 CEFR 阶梯 A1/C2", ("A1" in pbody and "C2" in pbody), "缺 CEFR 节点")
        check("/path 有锁定/完成节点", ("未解锁" in pbody or "Locked" in pbody), "缺锁定态")
        with app.app_context():
            from models import WordMeta
            total_meta = WordMeta.query.count()
            check("词库 CEFR 分级已回填（>=1000 词）", total_meta >= 1000, total_meta)
        rlang = c.client.get("/set-lang?lang=en", follow_redirects=False)
        cookie = rlang.headers.get("Set-Cookie", "")
        check("/set-lang?lang=en 写入 ui_lang Cookie", "ui_lang=en" in cookie, cookie[:120])
        check("/learn?cefr=A1 正常 200", c.get("/learn?cefr=A1").status_code == 200)
        check("/learn?cefr=C2 正常 200", c.get("/learn?cefr=C2").status_code == 200)

        print("\n[6] V5 平台：Onboarding / Courses / Review / 粤语")
        check("/onboarding 200", c.get("/onboarding").status_code == 200)
        check("/courses 200 且含 Cantonese",
              c.get("/courses").status_code == 200 and "Cantonese" in c.get("/courses").get_data(as_text=True))
        check("/review 200（Today's Review）",
              c.get("/review").status_code == 200 and "Review" in c.get("/review").get_data(as_text=True))
        ry = c.client.get("/learn/yue", follow_redirects=False)
        check("/learn/yue 跳转粤语 Unit", ry.status_code in (301, 302) and "/unit/" in (ry.headers.get("Location") or ""),
              f"{ry.status_code} {ry.headers.get('Location')}")
        rdash = c.get("/dashboard")
        check("Dashboard 含 Today's Review 条", "Review" in rdash.get_data(as_text=True))

        print("\n" + "=" * 62)
        print(f"结果：通过 {PASS} / 失败 {FAIL}")
        print("=" * 62)
        sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    main()
