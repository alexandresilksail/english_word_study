"""业务逻辑层：出题、判分、进度统计。

所有涉及用户数据的查询都必须带 user_id 过滤，确保用户之间完全隔离。
"""
from __future__ import annotations

import random
from datetime import datetime, timedelta, timezone

from sqlalchemy import case, func

from extensions import db
from localization import pick
from models import (Favorite, StudyRecord, TestRecord, UserWordProgress, Word,
                    WrongAnswer, utcnow)

# --------------------------------------------------------------------------
# 辅助
# --------------------------------------------------------------------------
STATUS_LABELS = {"new": "未学习", "learning": "学习中", "mastered": "已掌握"}
STATUS_LABELS_EN = {"new": "New", "learning": "Learning", "mastered": "Mastered"}


def today_start():
    """当天零点（UTC，与 utcnow() 保持一致，避免跨时区统计错位）。"""
    return datetime.now(timezone.utc).replace(tzinfo=None, hour=0, minute=0,
                                              second=0, microsecond=0)


def _fmt_dt(dt):
    if not dt:
        return ""
    return dt.strftime("%Y-%m-%d %H:%M")


def build_word_view(word: Word, user_id: int) -> dict:
    """把一个 Word 转成前端展示用的 dict（含当前用户的个性化状态）。"""
    prog = UserWordProgress.query.filter_by(user_id=user_id, word_id=word.id).first()
    fav = Favorite.query.filter_by(user_id=user_id, word_id=word.id).first()
    wrong = WrongAnswer.query.filter_by(user_id=user_id, word_id=word.id).first()
    return {
        "id": word.id,
        "word": word.word,
        "phonetic_uk": word.phonetic_uk,
        "pos": word.pos,
        "meaning_cn": word.meaning_cn,
        "example_en": word.example_en,
        "example_cn": word.example_cn,
        "audio": word.audio,
        "level": word.level,
        "initial": word.initial,
        "status": prog.status if prog else "new",
        "status_label": pick(STATUS_LABELS_EN.get(prog.status if prog else "new", "New"),
                              STATUS_LABELS.get(prog.status if prog else "new", "未学习")),
        "correct": prog.correct_count if prog else 0,
        "wrong": prog.wrong_count if prog else 0,
        "view_count": prog.view_count if prog else 0,
        "fav": bool(fav),
        "in_wrong": bool(wrong),
        "wrong_count": wrong.wrong_count if wrong else 0,
        "last_studied": _fmt_dt(prog.last_studied_at) if prog else "",
    }


# --------------------------------------------------------------------------
# 学习行为记录
# --------------------------------------------------------------------------
def touch_word(user_id: int, word_id: int, action: str = "view") -> None:
    """记录一次学习活动（浏览 / 发音 / 收藏等），更新进度表。"""
    prog = UserWordProgress.query.filter_by(user_id=user_id, word_id=word_id).first()
    if not prog:
        prog = UserWordProgress(user_id=user_id, word_id=word_id, status="learning",
                                first_learned_at=utcnow())
        db.session.add(prog)
    prog.last_studied_at = utcnow()
    if action == "view":
        prog.view_count = (prog.view_count or 0) + 1
    if prog.status == "new":
        prog.status = "learning"
    db.session.add(StudyRecord(user_id=user_id, word_id=word_id, action=action))
    db.session.commit()


def record_answer(user_id: int, word_id: int, correct: bool, mode: str = "") -> None:
    """记录一次答题结果：更新掌握度、错题本、学习流水。"""
    prog = UserWordProgress.query.filter_by(user_id=user_id, word_id=word_id).first()
    if not prog:
        prog = UserWordProgress(user_id=user_id, word_id=word_id,
                                first_learned_at=utcnow())
        db.session.add(prog)
    prog.last_studied_at = utcnow()

    if correct:
        prog.correct_count = (prog.correct_count or 0) + 1
    else:
        prog.wrong_count = (prog.wrong_count or 0) + 1

    total = (prog.correct_count or 0) + (prog.wrong_count or 0)
    acc = (prog.correct_count or 0) * 100 / total if total else 0
    if (prog.correct_count or 0) >= 3 and acc >= 80:
        prog.status = "mastered"
        if not prog.mastered_at:
            prog.mastered_at = utcnow()
    elif prog.status == "new":
        prog.status = "learning"

    if correct:
        row = WrongAnswer.query.filter_by(user_id=user_id, word_id=word_id).first()
        if row:
            db.session.delete(row)   # 答对即从错题本移除
    else:
        row = WrongAnswer.query.filter_by(user_id=user_id, word_id=word_id).first()
        if row:
            row.wrong_count = (row.wrong_count or 0) + 1
            row.last_wrong_at = utcnow()
        else:
            db.session.add(WrongAnswer(user_id=user_id, word_id=word_id,
                                       first_wrong_at=utcnow(), last_wrong_at=utcnow()))
    db.session.add(StudyRecord(user_id=user_id, word_id=word_id, action="answer",
                               mode=mode, is_correct=correct))
    db.session.commit()


# --------------------------------------------------------------------------
# 出题
# --------------------------------------------------------------------------
def _pick_word_ids(user_id: int, scope: str, size: int) -> list[int]:
    q = db.session.query(Word.id)
    if scope == "favorites":
        q = q.join(Favorite, Favorite.word_id == Word.id).filter(Favorite.user_id == user_id)
    elif scope == "wrong":
        q = q.join(WrongAnswer, WrongAnswer.word_id == Word.id).filter(WrongAnswer.user_id == user_id)
    elif scope == "new":
        studied = db.session.query(UserWordProgress.word_id).filter(UserWordProgress.user_id == user_id)
        q = q.filter(~Word.id.in_(studied))
    elif scope in ("learning", "mastered"):
        q = q.join(UserWordProgress, UserWordProgress.word_id == Word.id) \
             .filter(UserWordProgress.user_id == user_id, UserWordProgress.status == scope)

    ids = [r[0] for r in q.all()]
    if not ids:
        return []
    random.shuffle(ids)
    return ids[:min(size, len(ids))]


def _question_rng(word_id: int, seed=None) -> random.Random:
    """为同一道题返回**确定性**的随机源。

    出题（GET /api/quiz/item）与判题（POST /api/quiz/answer）会各自调用一次
    build_question()，两次必须得到完全相同的选项集合与顺序；否则前端提交的
    「选项下标」在后端会指向另一个选项，表现为"点了正确答案却提示错误"。
    seed 由一次测试会话提供：同一题在同一轮内可复现，跨轮次依然有变化。
    """
    base = (int(seed) if seed is not None else 0) * 1000003 + int(word_id)
    return random.Random(base)


def _distractors(word: Word, n: int, field: str, rng: random.Random) -> list[str]:
    """取 n 个干扰项（同字段内容）。

    - 使用传入的 rng 而不是全局 random，保证同一题重复调用结果一致；
    - 以正确答案文本为初始 seen 集合，避免出现与正确答案完全相同的干扰项
      （不同单词可能有相同释义），否则会出现两个一模一样的选项。
    """
    correct = getattr(word, field)
    rows = db.session.query(Word.id, getattr(Word, field)).filter(
        Word.id != word.id, func.length(getattr(Word, field)) > 0
    ).all()
    rng.shuffle(rows)
    vals, seen = [], {correct}
    for _wid, v in rows:
        if v and v not in seen:
            seen.add(v)
            vals.append(v)
        if len(vals) >= n:
            break
    return vals


def build_question(word: Word, mode: str, seed=None) -> dict:
    """按模式生成一道题（含 4 个选项，顺序打乱）。

    seed 不变时，同一道题（同一 word）的输出完全一致——这是判分能对上号的
    前提，详见 _question_rng()。
    """
    rng = _question_rng(word.id, seed)
    if mode == "choice":                       # 英文 → 中文
        opts = _distractors(word, 3, "meaning_cn", rng) + [word.meaning_cn]
        correct_text = word.meaning_cn
        rng.shuffle(opts)
        return {
            "mode": "choice", "word_id": word.id, "word": word.word,
            "stem": word.word, "stem_sub": word.phonetic_uk,
            "options": opts, "answer": correct_text,
        }
    if mode == "zh_en":                        # 中文 → 英文
        opts = _distractors(word, 3, "word", rng) + [word.word]
        correct_text = word.word
        rng.shuffle(opts)
        return {
            "mode": "zh_en", "word_id": word.id, "word": word.word,
            "stem": word.meaning_cn, "stem_sub": word.pos or "英文单词",
            "options": opts, "answer": correct_text,
        }
    if mode == "listen":                       # 听音 → 选单词
        opts = _distractors(word, 3, "word", rng) + [word.word]
        correct_text = word.word
        rng.shuffle(opts)
        return {
            "mode": "listen", "word_id": word.id, "word": word.word,
            "stem": "", "stem_sub": "", "audio": word.audio,
            "options": opts, "answer": correct_text,
        }
    # spell：中文释义 → 拼写英文
    return {
        "mode": "spell", "word_id": word.id, "word": word.word,
        "stem": word.meaning_cn, "stem_sub": word.phonetic_uk,
        "first": word.word[0], "length": len(word.word),
        "answer": word.word,
    }


def judge(question: dict, user_answer: str) -> bool:
    """判分。拼写模式做宽松匹配（忽略大小写与首尾空格，忽略 to + 冠词前缀）。"""
    expected = (question.get("answer") or "").strip().lower()
    got = (user_answer or "").strip().lower()
    if question.get("mode") == "spell":
        for prefix in ("to ", "a ", "an ", "the "):
            if got.startswith(prefix):
                got = got[len(prefix):].strip()
        return got == expected
    return got == expected


# --------------------------------------------------------------------------
# 统计
# --------------------------------------------------------------------------
def dashboard_stats(user_id: int) -> dict:
    total_words = Word.query.count()
    prog = UserWordProgress.query.filter_by(user_id=user_id)
    learned = prog.filter(UserWordProgress.status.in_(["learning", "mastered"])).count()
    mastered = prog.filter_by(status="mastered").count()
    favorites = Favorite.query.filter_by(user_id=user_id).count()
    wrong = WrongAnswer.query.filter_by(user_id=user_id).count()

    answers_q = StudyRecord.query.filter_by(user_id=user_id, action="answer")
    total_answers = answers_q.count()
    total_correct = answers_q.filter(StudyRecord.is_correct.is_(True)).count()

    today = today_start()
    today_answers = answers_q.filter(StudyRecord.created_at >= today).count()
    today_correct = answers_q.filter(StudyRecord.created_at >= today,
                                     StudyRecord.is_correct.is_(True)).count()
    tests = TestRecord.query.filter_by(user_id=user_id).count()
    views = StudyRecord.query.filter_by(user_id=user_id, action="view").count()

    percent = round(learned * 100 / total_words) if total_words else 0
    accuracy = round(total_correct * 100 / total_answers) if total_answers else 0

    return {
        "total_words": total_words, "learned": learned, "mastered": mastered,
        "new_words": total_words - learned, "favorites": favorites, "wrong": wrong,
        "total_answers": total_answers, "total_correct": total_correct,
        "today_answers": today_answers, "today_correct": today_correct,
        "today_accuracy": round(today_correct * 100 / today_answers) if today_answers else 0,
        "tests": tests, "views": views,
        "progress_percent": percent, "accuracy": accuracy,
        "mastered_percent": round(mastered * 100 / total_words) if total_words else 0,
    }


def daily_trend(user_id: int, days: int = 7) -> list[dict]:
    start = today_start() - timedelta(days=days - 1)
    rows = db.session.query(
        func.date(StudyRecord.created_at).label("day"),
        func.count(StudyRecord.id).label("total"),
        func.sum(case((StudyRecord.is_correct.is_(True), 1), else_=0)).label("correct"),
    ).filter(StudyRecord.user_id == user_id, StudyRecord.action == "answer",
             StudyRecord.created_at >= start).group_by(func.date(StudyRecord.created_at)).all()
    lookup = {str(r[0]): (r[1], int(r[2] or 0)) for r in rows}
    out = []
    for i in range(days):
        d = (today_start() - timedelta(days=days - 1 - i)).strftime("%Y-%m-%d")
        t, c = lookup.get(d, (0, 0))
        out.append({"day": d, "label": d[5:], "total": t, "correct": c,
                    "percent": round(c * 100 / t) if t else 0})
    return out


def learning_streak(user_id: int) -> int:
    """连续学习天数。"""
    days = {str(r[0]) for r in db.session.query(
        func.date(StudyRecord.created_at)).filter(
        StudyRecord.user_id == user_id).all()}
    streak, cursor = 0, today_start()
    while True:
        if cursor.strftime("%Y-%m-%d") in days:
            streak += 1
            cursor -= timedelta(days=1)
            continue
        # 今天还没学，从昨天开始数
        if cursor == today_start() and streak == 0:
            cursor -= timedelta(days=1)
            if cursor.strftime("%Y-%m-%d") in days:
                streak = 1
                cursor -= timedelta(days=1)
                continue
        break
    return streak


# (icon, name_zh, name_en, name_alt, test, desc_zh, desc_en)
BADGES = [
    ("🌱", "扬帆起航", "First Steps", "Set Sail",
     lambda s: s["learned"] >= 1, "学习第 1 个单词", "Learned your first word"),
    ("🔖", "小有收藏", "Bookmarker", "Collector",
     lambda s: s["favorites"] >= 10, "收藏 10 个单词", "Saved 10 words"),
    ("🔥", "勤学苦练", "Diligent", "Hard Worker",
     lambda s: s["total_answers"] >= 50, "累计答题 50 次", "Answered 50 questions"),
    ("🎯", "神射手", "Sharpshooter", "Marksman",
     lambda s: s["accuracy"] >= 90 and s["total_answers"] >= 20, "正确率 ≥ 90%", "Accuracy ≥ 90%"),
    ("👑", "单词之王", "Word King", "Vocabulary Monarch",
     lambda s: s["mastered"] >= 100, "掌握 100 个单词", "Mastered 100 words"),
    ("📚", "博学多才", "Scholar", "Learned",
     lambda s: s["mastered"] >= 500, "掌握 500 个单词", "Mastered 500 words"),
    ("🏆", "千锤百炼", "Iron Will", "Tempered",
     lambda s: s["tests"] >= 10, "完成 10 次测试", "Completed 10 tests"),
    ("💎", "持之以恒", "Perseverant", "Persistent",
     lambda s: s.get("streak", 0) >= 3, "连续学习 3 天", "Studied 3 days in a row"),
]


def unlocked_badges(stats: dict) -> list[dict]:
    out = []
    for icon, name_zh, name_en, name_alt, test, desc_zh, desc_en in BADGES:
        try:
            got = bool(test(stats))
        except Exception:
            got = False
        out.append({"icon": icon, "name": name_zh, "name_en": name_en,
                    "name_alt": name_alt, "desc": desc_zh, "desc_en": desc_en,
                    "unlocked": got})
    return out
