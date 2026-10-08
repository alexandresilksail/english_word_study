"""English 版面中文泄漏扫描器。

用 Flask 测试客户端登录后，对每个路由以 ui_lang=en 请求，
检测响应 HTML 中是否残留 CJK 中文字符（含已剥离 .bi-zh 之后仍残留的）。
"""
from __future__ import annotations

import os
import re
import sys

BASE_DIR = os.path.abspath(os.path.dirname(__file__))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from config import TestingConfig  # noqa: E402
from app import create_app  # noqa: E402
from extensions import db  # noqa: E402
from models import Course, Unit, Lesson, User  # noqa: E402

CJK = re.compile(r"[一-鿿]")


def cjk_snippet(text: str, maxn: int = 3) -> str:
    out = []
    for m in CJK.finditer(text):
        s = max(0, m.start() - 12)
        e = min(len(text), m.end() + 12)
        out.append(text[s:e].replace("\n", " "))
        if len(out) >= maxn:
            break
    return " | ".join(out)


def main():
    app = create_app(TestingConfig)
    app.config["WTF_CSRF_ENABLED"] = False
    with app.app_context():
        db.create_all()
        from seeds.seed_words import seed_from_json
        seed_from_json(app.config["WORDS_JSON"])

        client = app.test_client()

        # 先切到 English 版面（写入 ui_lang cookie），再注册/登录，
        # 否则注册成功 flash 会在 zh 语境下落库中文，污染 en 扫描结果
        client.get("/set-lang?lang=en")

        # 注册 + 登录
        email = "scanner@demo.com"
        if not User.query.filter_by(email=email).first():
            client.post("/register",
                        data={"email": email, "username": "Scanner",
                              "password": "Scan123456", "confirm": "Scan123456"})
        client.post("/login", data={"email": email, "password": "Scan123456"})

        course = Course.query.first()
        unit = Unit.query.first()
        lesson = Lesson.query.first()
        cid = course.id if course else 1
        uid = unit.id if unit else 1
        lid = lesson.id if lesson else 1

        routes = [
            "/", "/login", "/register", "/courses", "/path", "/path/en", "/path/yue",
            "/learn/en", "/learn/yue", "/onboarding", "/healthz",
            "/dashboard", "/learn", "/words", "/progress", "/profile",
            "/favorites", "/wrong", "/test", "/review", "/ai-tutor",
            f"/course/{cid}", f"/unit/{uid}", f"/lesson/{lid}",
            f"/unit/{uid}/test",
            "/practice", "/settings", "/quiz",
            "/learn/hub", "/learn/skill/vocabulary", "/learn/skill/listening",
            "/learn/skill/reading", "/learn/skill/grammar",
            "/learn/skill/speaking", "/learn/skill/writing",
            "/words/learn", "/word/1",
            "/games", "/podcast", "/games/word_match",
        ]

        # 用内置 /set-lang 路由写入 ui_lang cookie（保留登录 session）
        client.get("/set-lang?lang=en")
        total_leaks = 0
        print("=== English 版面 (ui_lang=en) 中文泄漏扫描 ===")
        for r in routes:
            try:
                resp = client.get(r, follow_redirects=True)
            except Exception as exc:  # pragma: no cover
                print(f"[ERR ] {r:24s} {exc}")
                continue
            body = resp.get_data(as_text=True)
            n = len(CJK.findall(body))
            if n:
                total_leaks += 1
                print(f"[LEAK] {r:24s} HTTP {resp.status_code}  CJK={n:4d}  e.g. {cjk_snippet(body)}")
            else:
                tag = "ok" if resp.status_code < 400 or resp.status_code == 404 else "?"
                print(f"[{tag:>3}] {r:24s} HTTP {resp.status_code}")

        # ---- 匿名（未登录）模式再扫一遍：捕捉未登录访问受保护页时，
        #      被重定向到 /login 后携带的中文 flash / 页面泄漏 ----
        anon = app.test_client()
        anon.get("/set-lang?lang=en")
        anon_leaks = 0
        print("\n=== 匿名 (anonymous, ui_lang=en) 中文泄漏扫描 ===")
        for r in routes:
            try:
                resp = anon.get(r, follow_redirects=True)
            except Exception as exc:  # pragma: no cover
                print(f"[ERR ] {r:24s} {exc}")
                continue
            body = resp.get_data(as_text=True)
            n = len(CJK.findall(body))
            if n:
                anon_leaks += 1
                print(f"[LEAK] {r:24s} HTTP {resp.status_code}  CJK={n:4d}  e.g. {cjk_snippet(body)}")
            else:
                print(f"[{'ok':>3}] {r:24s} HTTP {resp.status_code}")

        logged_leaks = total_leaks
        total_leaks += anon_leaks
        print(f"\n扫描完成：发现 {total_leaks} 个路由存在中文残留"
              f"（登录态 {logged_leaks} + 匿名 {anon_leaks}）。")


if __name__ == "__main__":
    main()
