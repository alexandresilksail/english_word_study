"""端到端功能测试（Flask 测试客户端，无需启动服务器）。

用法：
    python tests/test_flow.py

覆盖：词库完整性 / 注册 / 登录 / 退出 / 登录保护 / 发音文件 / 学习 /
      收藏 / 错题 / 四种测试模式 / 用户数据隔离 / CSRF / 管理员权限 /
      登录失败锁定 / 重新登录
测试用的 helper 会在 CSRF 关闭的 app 实例里跑主流程，
另有独立的 csrf 校验小节使用开启 CSRF 的实例。
"""
from __future__ import annotations

import os
import re
import sys

BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from config import TestingConfig  # noqa: E402
from app import create_app  # noqa: E402
from extensions import db  # noqa: E402
from models import (Favorite, StudyRecord, TestRecord, User, UserWordProgress,  # noqa: E402
                    Word, WrongAnswer)

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
    return app


class CleanWrapper:
    """测试客户端包装。

    注意：测试脚本外层持有 app_context()，Flask 会复用该 app context，
    Flask-Login 缓存在 g._login_user 的用户会被后续请求看到。
    生产环境每个请求都会新建 app context，不存在该问题；
    这里在每次请求前清理 g，保证测试 client 之间真正相互隔离。
    """

    def __init__(self, app):
        self.app = app
        self.client = app.test_client()

    def _reset(self):
        # 必须在外层那个共享的 app context 上操作 g，
        # 不能再嵌套 app_context()（那样会新建 context，清理不到）
        try:
            from flask import g
            for key in ("_login_user", "_fresh"):
                if hasattr(g, key):
                    g.pop(key)
        except Exception as exc:  # pragma: no cover
            print("reset error:", exc)

    def get(self, *a, **k):
        self._reset()
        return self.client.get(*a, **k)

    def post(self, *a, **k):
        self._reset()
        return self.client.post(*a, **k)


def C(app):
    return CleanWrapper(app)


def join(c, email, username, pw=PW, tok=None):
    payload = {"email": email, "username": username, "password": pw, "confirm": pw}
    if tok:
        payload["csrf_token"] = tok
    return c.post("/register", data=payload)


def signin(c, email, pw=PW, tok=None):
    payload = {"email": email, "password": pw}
    if tok:
        payload["csrf_token"] = tok
    return c.post("/login", data=payload)


def token_of(html: str) -> str:
    m = re.search(r'name="csrf_token"[^>]*value="([^"]+)"', html)
    return m.group(1) if m else ""


def main():
    app = build(False)

    with app.app_context():
        print("\n[0] 词库完整性")
        total = Word.query.count()
        check("词库 2000 条", total == 2000, total)
        letters = {r[0] for r in db.session.query(Word.initial).all()}
        missing = sorted(set("abcdefghijklmnopqrstuvwxyz") - letters)
        check("A-Z 全覆盖", not missing, missing)
        blanks = db.session.query(Word.word).filter(
            (Word.meaning_cn == "") | (Word.phonetic_uk == "") |
            (Word.example_en == "") | (Word.example_cn == "")).all()
        check("无空数据", not blanks, [b[0] for b in blanks[:5]])
        words_at_limit = db.session.query(Word.word).filter(Word.word == "all").count()
        check("单词唯一", words_at_limit <= 1)

        print("\n[1] 注册")
        ca = C(app)
        r = join(ca, "alice@demo.com", "用户甲")
        check("注册成功并自动登录", r.status_code == 302, r.status_code)
        alice = User.query.filter_by(email="alice@demo.com").first()
        check("用户落库", alice is not None)
        check("密码哈希保存（非明文）",
              alice is not None and alice.password_hash and len(alice.password_hash) > 50
              and alice.password_hash != PW)
        check("普通用户非管理员", alice is not None and not alice.is_admin)
        check("重复邮箱被拒绝",
              User.query.filter_by(email="alice@demo.com").count() == 1
              and join(C(app), "alice@demo.com", "重复用户").status_code == 200)
        join(C(app), "weak@demo.com", "弱密码用户", "12345678")
        check("弱密码被拒绝", User.query.filter_by(email="weak@demo.com").first() is None)

        print("\n[2] 登录")
        cb = C(app)
        r = signin(cb, "alice@demo.com")
        loc = r.headers.get("Location") or ""
        check("登录成功跳转", r.status_code == 302 and "/dashboard" in loc, loc)
        check("错误密码失败", signin(C(app), "alice@demo.com", "wrongpwd").status_code == 200)
        check("登录后 dashboard 200", cb.get("/dashboard").status_code == 200)

        print("\n[3] 未登录保护")
        anon = C(app)
        for path in ("/dashboard", "/learn", "/words", "/progress", "/favorites",
                     "/wrong", "/test", "/profile"):
            r = anon.get(path)
            loc = r.headers.get("Location") or ""
            check(f"匿名 {path} 被拦截", r.status_code == 302 and "/login" in loc,
                  f"{r.status_code} {loc}")
        r = anon.post("/api/quiz/start", json={"mode": "choice"})
        check("匿名 API 被拦截", r.status_code in (302, 401), r.status_code)
        check("首页匿名可访问", anon.get("/").status_code == 200)
        check("健康检查可用", anon.get("/healthz").json.get("status") == "ok")

        print("\n[4] 第二个用户（数据隔离基线）")
        join(C(app), "bob@demo.com", "用户乙")
        bob = User.query.filter_by(email="bob@demo.com").first()
        c_bob = C(app)
        signin(c_bob, "bob@demo.com")
        check("用户乙可访问主页", c_bob.get("/dashboard").status_code == 200)

        print("\n[5] 学习与发音")
        check("学习页 200", cb.get("/learn").status_code == 200)
        check("单词本 200", cb.get("/words").status_code == 200)
        check("搜索页 200", cb.get("/words?q=water").status_code == 200)
        check("字母筛选 200", cb.get("/words?letter=s").status_code == 200)
        word = Word.query.filter_by(word="water").first() or Word.query.first()
        check("详情页 200", cb.get(f"/word/{word.id}").status_code == 200)
        check("浏览写入学习流水", StudyRecord.query.filter_by(user_id=alice.id).count() > 0)
        check("浏览建立进度记录", UserWordProgress.query.filter_by(user_id=alice.id).count() > 0)
        audio_dir = os.path.join(BASE_DIR, "app", "static", "audio")
        miss = [w.word for w in Word.query.limit(60).all()
                if not os.path.exists(os.path.join(audio_dir, w.audio))]
        check("本地音频齐备（抽样 60）", not miss, miss[:5])
        size = os.path.getsize(os.path.join(audio_dir, word.audio))
        check("音频文件有效", size > 500, size)
        r = cb.get(f"/static/audio/{word.audio}")
        check("静态音频可访问", r.status_code == 200 and "audio" in (r.content_type or ""),
              f"{r.status_code} {r.content_type}")

        print("\n[6] 收藏")
        cb.post("/action/toggle-favorite", data={"word_id": word.id})
        check("收藏成功", Favorite.query.filter_by(user_id=alice.id, word_id=word.id).first() is not None)
        cb.post("/action/toggle-favorite", data={"word_id": word.id})
        check("取消收藏成功", Favorite.query.filter_by(user_id=alice.id, word_id=word.id).first() is None)
        cb.post("/action/toggle-favorite", data={"word_id": word.id})
        cb.post("/action/set-status", data={"word_id": word.id, "status": "mastered"})
        prog = UserWordProgress.query.filter_by(user_id=alice.id, word_id=word.id).first()
        check("标记已掌握", prog is not None and prog.status == "mastered")

        print("\n[7] 四种测试模式")
        for mode in ("choice", "zh_en", "listen", "spell"):
            c = C(app)
            signin(c, "alice@demo.com")
            r = c.post("/api/quiz/start", json={"mode": mode, "scope": "all", "size": 6})
            st = r.get_json()
            check(f"[{mode}] 开始测试", bool(st and st.get("ok")), st)
            ok_all = True
            wrong_first = False
            for i in range(st["total"]):
                it = c.get(f"/api/quiz/item?i={i}").get_json()
                if not it or not it.get("ok"):
                    ok_all = False
                    break
                q = it["q"]
                target = Word.query.get(q["word_id"])
                if mode == "spell":
                    if i == 0 and not wrong_first:
                        ans = "zzzzz"
                        wrong_first = True
                    else:
                        ans = target.word
                elif mode == "choice":
                    want = target.meaning_cn
                    ans = q["options"].index(want) if want in q["options"] else 0
                else:
                    want = target.word
                    if i == 0 and not wrong_first:
                        ans = 0 if q["options"][0] != want else 1
                        wrong_first = True
                    else:
                        ans = q["options"].index(want) if want in q["options"] else 0
                res = c.post("/api/quiz/answer", json={"i": i, "answer": ans}).get_json()
                if not res or not res.get("ok"):
                    ok_all = False
                    break
            check(f"[{mode}] 逐题作答正常", ok_all)
            fin = c.post("/api/quiz/finish", json={}).get_json()
            check(f"[{mode}] 完成并写入测试记录",
                  bool(fin and fin.get("ok") and fin.get("total") == 6), fin)
        check("测试记录已落库", TestRecord.query.filter_by(user_id=alice.id).count() >= 4)
        check("答错进入错题本", WrongAnswer.query.filter_by(user_id=alice.id).count() > 0)
        check("错题本页 200", cb.get("/wrong").status_code == 200)

        print("\n[8] 用户数据隔离")
        check("甲有收藏", Favorite.query.filter_by(user_id=alice.id).count() > 0)
        check("乙无收藏", Favorite.query.filter_by(user_id=bob.id).count() == 0)
        check("乙无错题", WrongAnswer.query.filter_by(user_id=bob.id).count() == 0)
        check("乙无测试记录", TestRecord.query.filter_by(user_id=bob.id).count() == 0)
        check("乙无学习流水", StudyRecord.query.filter_by(user_id=bob.id).count() == 0)
        check("乙无进度", UserWordProgress.query.filter_by(user_id=bob.id).count() == 0)
        body = c_bob.get("/favorites").get_data(as_text=True)
        check("乙的收藏页为空状态", "收藏夹还是空的" in body)
        body = c_bob.get("/wrong").get_data(as_text=True)
        check("乙的错题页为空状态", "错题本是空的" in body)

        print("\n[9] 其它页面")
        for path in ("/progress", "/profile", "/learn?scope=new", "/learn?scope=mastered",
                     "/favorites", "/test?mode=spell"):
            check(f"{path} 200", cb.get(path).status_code == 200)
        body = cb.get("/progress").get_data(as_text=True)
        check("进度页渲染统计", "学习进度" in body and "掌握度分布" in body)

        print("\n[10] CSRF 防护（开启 CSRF 的实例）")
        csrf_app = build(True)
        with csrf_app.app_context():
            cc = C(csrf_app)
            html = cc.get("/register").get_data(as_text=True)
            tok = token_of(html)
            check("注册页含 CSRF token", len(tok) > 20, len(tok))
            r = C(csrf_app).post("/register", data={"email": "xx@demo.com",
                                                    "username": "无令牌",
                                                    "password": PW, "confirm": PW})
            check("无 CSRF token 被拒", r.status_code == 400, r.status_code)
            r = join(cc, "carol@demo.com", "用户丙", tok=tok)
            check("带 CSRF token 注册成功", r.status_code == 302, r.status_code)
            wp = cc.get("/words").get_data(as_text=True)
            tok2 = token_of(wp)
            check("单词页含 CSRF token", len(tok2) > 20, len(tok2))
            r = cc.post("/action/toggle-favorite", data={"word_id": 1})
            check("缺 CSRF 的 POST 被拒", r.status_code == 400, r.status_code)
            r = cc.post("/action/toggle-favorite", data={"csrf_token": tok2, "word_id": 1})
            check("带 CSRF 的 POST 通过", r.status_code in (302, 200), r.status_code)
            carol = User.query.filter_by(email="carol@demo.com").first()
            check("收藏功能实际生效", carol is not None
                  and Favorite.query.filter_by(user_id=carol.id).count() > 0)

        print("\n[11] 管理员后台")
        check("普通用户访问后台被拒", cb.get("/admin/").status_code == 403)
        admin = User(email="root@demo.com", username="管理员", is_admin=True)
        admin.set_password("Root123456")
        db.session.add(admin)
        db.session.commit()
        cadm = C(app)
        signin(cadm, "root@demo.com", "Root123456")
        r = cadm.get("/admin/")
        check("管理员进入后台", r.status_code == 200, r.status_code)
        body = r.get_data(as_text=True)
        for key in ("注册用户数", "词库单词数", "累计答题次数", "累计测试次数", "今日活跃用户"):
            check(f"后台显示 {key}", key in body)
        check("后台用户明细 200", cadm.get("/admin/users").status_code == 200)
        stats = cadm.get("/admin/api/stats").get_json()
        check("后台 API 返回统计", stats.get("words") == 2000 and stats.get("users") >= 3, stats)

        print("\n[12] 退出登录 / 重新登录")
        r = cb.get("/logout")
        check("退出后重定向首页", r.status_code == 302 and (r.headers.get("Location") or "").endswith("/"),
              r.headers.get("Location"))
        check("退出后 dashboard 不可访问", cb.get("/dashboard").status_code == 302)
        cn = C(app)
        signin(cn, "bob@demo.com")
        check("重新登录成功", cn.get("/dashboard").status_code == 200)

        print("\n[13] 登录失败锁定")
        lv = C(app)
        for _ in range(6):
            signin(lv, "alice@demo.com", "bad-password")
        locked = User.query.filter_by(email="alice@demo.com").first()
        check("连续失败触发锁定", locked.locked_until is not None, locked.locked_until)
        r = signin(lv, "alice@demo.com")
        check("锁定期间正确密码也无法登录", r.status_code == 200, r.status_code)
        locked.locked_until = None
        locked.failed_logins = 0
        db.session.commit()
        check("解锁后可重新登录", signin(C(app), "alice@demo.com").status_code == 302)

        print("\n[14] 个人资料")
        cp = C(app)
        signin(cp, "alice@demo.com")
        r = cp.post("/profile", data={"form_kind": "profile", "username": "新名字"})
        check("修改用户名成功",
              User.query.filter_by(email="alice@demo.com").first().username == "新名字",
              r.status_code)
        r = cp.post("/profile", data={"form_kind": "password",
                                      "current_password": PW,
                                      "new_password": "NewPass123456",
                                      "confirm": "NewPass123456"})
        u = User.query.filter_by(email="alice@demo.com").first()
        check("修改密码成功", u.check_password("NewPass123456"), r.status_code)
        check("旧密码失效", not u.check_password(PW))

    print("\n" + "=" * 62)
    print(f"结果：通过 {PASS} / 失败 {FAIL}")
    print("=" * 62)
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    main()
