"""测试中心判分正确性回归测试。

用法：
    python tests/test_quiz_grading.py

背景（真实线上 bug，用户截图反馈）：
    前端点击选项后提交的是「选项下标」（data-i），而后端
    GET /api/quiz/item（出题）与 POST /api/quiz/answer（判题）会各自调用
    一次 build_question()。旧实现在这两次调用中都重新 random.shuffle(选项)
    且用 order_by(func.random()) 重新抽取干扰项，于是两次的 A/B/C/D 完全
    不是同一套，用户点中正确答案却提示 "Incorrect"。

    更隐蔽的是：前端错误框里同时显示 expected（正确答案）与 word.meaning
    （同一个词的中文释义），两串文字一模一样，看起来像"答案对却被判错"。

本测试锁死该链路：按下标提交正确答案必须判对，且判分文本要能对上。
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
from models import Word  # noqa: E402

PASS = FAIL = 0
PW = "Study123456"
EMAIL = "grader@demo.com"

CHOICE_MODES = ("choice", "zh_en", "listen")
ALL_MODES = ("choice", "zh_en", "listen", "spell")


def check(name, cond, extra=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("  PASS  " + name)
    else:
        FAIL += 1
        print("  FAIL  " + name + ("  << " + str(extra) if extra else ""))


class Client:
    """测试客户端包装：每次请求前清理 Flask-Login 在 g 上的缓存。"""

    def __init__(self, app):
        self.app = app
        self.c = app.test_client()

    def _reset(self):
        try:
            from flask import g
            for key in ("_login_user", "_fresh"):
                if hasattr(g, key):
                    g.pop(key)
        except Exception:  # pragma: no cover
            pass

    def get(self, *a, **k):
        self._reset()
        return self.c.get(*a, **k)

    def post(self, *a, **k):
        self._reset()
        return self.c.post(*a, **k)


def correct_text_of(word: Word, mode: str) -> str:
    """某一模式下该词的正确选项文本。"""
    if mode == "choice":            # 英文 → 中文
        return word.meaning_cn
    return word.word                # 中文 → 英文 / 听音选词 / 拼写


def main():
    app = create_app(TestingConfig)
    app.config["WTF_CSRF_ENABLED"] = False
    app.config["SERVER_NAME"] = None
    with app.app_context():
        db.create_all()
        from seeds.seed_words import seed_from_json
        seed_from_json(app.config["WORDS_JSON"])

    c = Client(app)

    with app.app_context():
        print("\n[0] 准备账号")
        r = c.post("/register", data={"email": EMAIL, "username": "判分测试",
                                      "password": PW, "confirm": PW})
        check("注册并登录", r.status_code == 302, r.status_code)

        # ------------------------------------------------------------------
        print("\n[1] 核心回归：提交正确选项必须判对（这是线上 bug 的直接复现）")
        for mode in ALL_MODES:
            st = c.post("/api/quiz/start",
                        json={"mode": mode, "scope": "all", "size": 10}).get_json()
            check(f"[{mode}] 开始测试", bool(st and st.get("ok")), st)
            if not st or not st.get("ok"):
                continue
            total = st["total"]
            all_right, first_bad, option_sets = True, None, []
            for i in range(total):
                it = c.get(f"/api/quiz/item?i={i}").get_json()
                if not it or not it.get("ok"):
                    all_right, first_bad = False, f"第{i+1}题取题失败"
                    break
                q = it["q"]
                word = db.session.get(Word, q["word_id"])
                want = correct_text_of(word, mode)

                if mode == "spell":
                    payload = want
                else:
                    opts = q["options"]
                    option_sets.append(list(opts))
                    if want not in opts:
                        all_right, first_bad = False, f"第{i+1}题选项里没有正确答案"
                        break
                    payload = opts.index(want)

                res = c.post("/api/quiz/answer",
                             json={"i": i, "answer": payload}).get_json()
                if not res or not res.get("correct"):
                    all_right = False
                    first_bad = (f"第{i+1}题 提交下标 {payload} 期望 "
                                 f"{want!r}，后端 expected={res.get('expected')!r} "
                                 f"user_answer={res.get('user_answer')!r}")
                    break

            check(f"[{mode}] {total} 题全部提交正确选项 → 全部判对", all_right, first_bad)
            if mode != "spell":
                dups = [s for s in option_sets if len(set(s)) != len(s)]
                check(f"[{mode}] 同一题四个选项无重复文本", not dups, dups[:2])
                check(f"[{mode}] 每题 4 个选项", all(len(s) == 4 for s in option_sets),
                      [len(s) for s in option_sets])

        # ------------------------------------------------------------------
        print("\n[2] 提交错误选项必须判错")
        for mode in CHOICE_MODES:
            c.post("/api/quiz/start", json={"mode": mode, "scope": "all", "size": 5})
            it = c.get("/api/quiz/item?i=0").get_json()
            q = it["q"]
            word = Word.query.get(q["word_id"])
            want = correct_text_of(word, mode)
            opts = q["options"]
            if want not in opts:
                check(f"[{mode}] 错误选项判错", False, "选项缺正确答案")
                continue
            wrong_idx = (opts.index(want) + 1) % len(opts)
            res = c.post("/api/quiz/answer",
                         json={"i": 0, "answer": wrong_idx}).get_json()
            check(f"[{mode}] 提交错误选项 → 判错",
                  bool(res and res.get("correct") is False), res)
            check(f"[{mode}] 判错时回传的 user_answer 是所选错误项",
                  bool(res and res.get("user_answer") == opts[wrong_idx]), res)

        # ------------------------------------------------------------------
        print("\n[3] 出题可复现性（判分能对上号的前提）")
        for mode in CHOICE_MODES + ("spell",):
            c.post("/api/quiz/start", json={"mode": mode, "scope": "all", "size": 5})
            a = c.get("/api/quiz/item?i=1").get_json()["q"]
            b = c.get("/api/quiz/item?i=1").get_json()["q"]
            if mode == "spell":
                check(f"[{mode}] 同一题重复取题一致",
                      a["stem"] == b["stem"] and a["word_id"] == b["word_id"])
            else:
                check(f"[{mode}] 同一题重复取题的选项顺序一致",
                      a["options"] == b["options"], (a.get("options"), b.get("options")))

        # 不同轮次（重新 start）应当有变化，否则题目顺序会被用户记住
        c.post("/api/quiz/start", json={"mode": "choice", "scope": "all", "size": 5})
        round1 = [c.get(f"/api/quiz/item?i={i}").get_json()["q"]["options"] for i in range(5)]
        c.post("/api/quiz/start", json={"mode": "choice", "scope": "all", "size": 5})
        round2 = [c.get(f"/api/quiz/item?i={i}").get_json()["q"]["options"] for i in range(5)]
        changed = sum(1 for x, y in zip(round1, round2) if x != y)
        check("新的一轮测试选项顺序会变化（seed 生效）", changed >= 1,
              f"5 题中仅 {changed} 题变化")

        # ------------------------------------------------------------------
        print("\n[4] 边界与兼容")
        c.post("/api/quiz/start", json={"mode": "choice", "scope": "all", "size": 3})
        q = c.get("/api/quiz/item?i=0").get_json()["q"]
        word = Word.query.get(q["word_id"])
        res = c.post("/api/quiz/answer",
                     json={"i": 0, "answer": q["options"].index(word.meaning_cn)}).get_json()
        check("取题后隔了若干次请求再按下标提交，仍能判对（seed 复现）",
              bool(res and res.get("correct") is True
                   and res.get("user_answer") == word.meaning_cn), res)
        res = c.post("/api/quiz/answer", json={"i": 0, "answer": 99}).get_json()
        check("越界下标（99）判错且不报异常",
              bool(res and res.get("ok") and res.get("correct") is False), res)

        # 直接提交文本（旧缓存前端 / 手工调用）也应能判对
        res = c.post("/api/quiz/answer",
                     json={"i": 0, "answer": word.meaning_cn}).get_json()
        check("提交正确选项文本同样判对（向后兼容）",
              bool(res and res.get("correct") is True), res)

        fin = c.post("/api/quiz/finish", json={}).get_json()
        check("测试可正常结算", bool(fin and fin.get("ok")), fin)

        print("\n[5] 教师视角：判分结果写入统计")
        from models import StudyRecord
        rows = StudyRecord.query.filter_by(action="answer").all()
        check("答题流水已落库", len(rows) > 0, len(rows))

    print("\n" + "=" * 60)
    print(f"结果：PASS {PASS} / FAIL {FAIL}")
    print("=" * 60)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
