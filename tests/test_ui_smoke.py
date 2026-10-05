"""UI 改造后的页面冒烟测试（与业务测试分离，专注呈现层）。

检查项：
1. 全部页面可正常渲染（200），无模板报错
2. 双语结构存在（class="bi" 且含 bi-en / bi-zh）
3. 未残留被禁止的游戏化元素（confetti / 彩带 / 吉祥物图）
4. 移动端底部标签栏存在、设计系统样式表被引入
5. 新版邮箱流程页面（忘记密码 / 重置）可访问
6. 单词卡具备 Word → Pronunciation → Meaning → Example → Action 五段结构

用法：
    python tests/test_ui_smoke.py
"""
from __future__ import annotations

import os
import sys

BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from app import create_app  # noqa: E402
from config import TestingConfig  # noqa: E402
from extensions import db  # noqa: E402

PASS = FAIL = 0
PW = "UiTest123456"
EMAIL = "ui_smoke@example.com"


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
    with app.app_context():
        db.create_all()
        from seeds.seed_words import seed_from_json
        seed_from_json(app.config["WORDS_JSON"])
    return app


def main():
    app = build()
    c = app.test_client()

    print("=" * 60)
    print("UI 冒烟测试")
    print("=" * 60)

    # ---------------------------------------------------------- 未登录可访问
    print("\n[1] 公开页面")
    for path in ["/", "/login", "/register", "/forgot-password"]:
        r = c.get(path)
        check(f"GET {path} → 200", r.status_code == 200, r.status_code)

    body = c.get("/login").get_data(as_text=True)
    check("登录页含双语结构 bi-en", 'class="bi-en"' in body)
    check("登录页含双语结构 bi-zh", 'class="bi-zh"' in body)
    check("登录页引入 design.css", "design.css" in body)
    check("登录页提供忘记密码入口", "/forgot-password" in body)

    # ---------------------------------------------------------- 注册并登录
    print("\n[2] 注册 / 登录")
    r = c.post("/register", data={"email": EMAIL, "username": "冒烟测试",
                                  "password": PW, "confirm": PW},
               follow_redirects=True)
    check("注册成功并进入系统", r.status_code == 200 and "学习中心" in r.get_data(as_text=True),
          r.status_code)
    if r.status_code != 200 or "学习中心" not in r.get_data(as_text=True):
        c.post("/login", data={"email": EMAIL, "password": PW}, follow_redirects=True)

    # ---------------------------------------------------------- 登录后页面
    print("\n[3] 登录后页面渲染")
    pages = ["/dashboard", "/learn", "/words", "/test", "/wrong", "/favorites", "/progress", "/profile"]
    for path in pages:
        r = c.get(path)
        check(f"GET {path} → 200", r.status_code == 200, r.status_code)

    # ---------------------------------------------------------- 双语与规范
    print("\n[4] 双语 / Design System 规范")
    dash = c.get("/dashboard").get_data(as_text=True)
    check("Dashboard 含双语结构", 'class="bi-en"' in dash and 'class="bi-zh"' in dash)
    check("Dashboard 含底部标签栏", 'class="tabbar"' in dash)
    check("Dashboard 未残留 confetti", "confetti" not in dash.lower())
    check("Dashboard 未残留吉祥物图", "mascot-owl" not in dash)
    check("Dashboard 未残留庆祝图", "celebrate.svg" not in dash)

    # ---------------------------------------------------------- 单词卡五段结构
    print("\n[5] 单词卡结构 Word → Pronunciation → Meaning → Example → Action")
    learn = c.get("/learn").get_data(as_text=True)
    for cls, label in [("wc-word", "Word 单词"),
                       ("wc-pron", "Pronunciation 发音"),
                       ("wc-meaning", "Meaning 释义"),
                       ("wc-example", "Example 例句"),
                       ("wc-actions", "Learning Action 操作")]:
        check(f"单词卡含 {label} 区块", cls in learn)

    # ---------------------------------------------------------- 单词详情 / 单词本
    print("\n[5b] 单词本与详情页")
    for path in ["/words", "/word/1"]:
        r = c.get(path)
        check(f"GET {path} → 200", r.status_code == 200, r.status_code)
    detail = c.get("/word/1").get_data(as_text=True)
    check("详情页含双语结构", 'class="bi-en"' in detail and 'class="bi-zh"' in detail)
    check("详情页未残留装饰插画", "hero-study" not in detail)

    # ---------------------------------------------------------- 邮箱验证 / 重置
    print("\n[6] 邮箱流程页面")
    # 已登录用户访问忘记密码应被引导回 Dashboard（正确行为，非缺陷）
    r = c.get("/forgot-password", follow_redirects=False)
    check("已登录访问忘记密码 → 重定向", r.status_code in (301, 302), r.status_code)
    prof = c.get("/profile").get_data(as_text=True)
    check("个人中心含邮箱验证状态", ("未验证" in prof) or ("已验证" in prof))
    check("个人中心含重发验证入口", "/resend-verification" in prof)

    # ---------------------------------------------------------- 退出
    print("\n[7] 退出登录")
    r = c.get("/logout", follow_redirects=True)
    check("退出后回到首页", r.status_code == 200, r.status_code)
    r2 = c.get("/dashboard", follow_redirects=False)
    check("退出后 dashboard 重定向", r2.status_code in (301, 302), r2.status_code)

    print("\n" + "=" * 60)
    print(f"结果：通过 {PASS} / 失败 {FAIL}")
    print("=" * 60)
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
