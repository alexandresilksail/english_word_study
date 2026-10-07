"""单元测验（Unit Test）服务 —— 规格 §5。

职责
----
1. 由 Unit 自己的 ContentItem **自动生成**测验（无需手写题库），幂等、可重建；
2. 判分并记录 ``score / accuracy / duration_sec / mistakes / weak_areas``；
3. 达到及格线 → 判定 **Mastered** 并把**下一单元解锁**；
   未达标 → 输出 **Weak Areas**，引导做针对性练习（Targeted Practice）后重测。

题目为什么这样出
----------------
English 版面必须只靠英文就能作答，所以题型按「英文数据是否可靠」排序：

1. ``cloze``   —— 例句挖空、选项是英文单词。**只需要 example_en**，
                  这是全库最可靠的英文字段，因此作为主力题型。
2. ``meaning`` —— 单词 → 英文释义（需要 ``meaning_en``），有就出、没有就跳过。
3. ``yue``     —— 粤语单元：给英文提示，选粤语词（English 版面同样可答）。

选项的中文侧照常写入 ``zh``，由模板的 ``pick`` 决定渲染哪一侧 ——
English 版面只看到 ``en``，**绝不出现中文**。
"""
from __future__ import annotations

import json
import logging
import random
import re

from extensions import db
from models import (ContentItem, Course, Lesson, Quiz, QuizQuestion, Unit,
                    UnitProgress, UnitTestAttempt)

logger = logging.getLogger(__name__)

#: 及格线（正确率 %）
PASS_SCORE = 70
#: 一套测验最多几题
MAX_QUESTIONS = 10
#: 每题选项数
N_OPTIONS = 4


def _utcnow():
    from models import utcnow
    return utcnow()


# --------------------------------------------------------------------------
# 出题
# --------------------------------------------------------------------------
def _shuffled(seq, rnd: random.Random):
    out = list(seq)
    rnd.shuffle(out)
    return out


def _dedup(seq):
    return list(dict.fromkeys([s for s in seq if s]))


def _cloze_blank(surface: str, sentence: str) -> str:
    """把例句里第一次出现的目标词替换成空位；词不在句里则返回空串。"""
    if not surface or not sentence:
        return ""
    pattern = re.compile(re.escape(surface), re.IGNORECASE)
    if not pattern.search(sentence):
        return ""
    return pattern.sub("_____", sentence, count=1)


def _build_questions(unit: Unit, items: list, lang_code: str) -> list[dict]:
    """按题型优先级生成题目字典列表（尚未入库）。"""
    rnd = random.Random(unit.id or 0)          # 按单元播种：同一套题每次一致
    out: list[dict] = []

    vocab = [it for it in items if it.kind == "vocabulary" and it.surface]
    word_pool = _dedup([it.surface for it in vocab])
    meaning_of_word = {it.surface: it.meaning_cn for it in vocab}

    # 1) 粤语单元：英文提示 → 选粤语词（surface 是粤语，meaning_en 是英文解释）
    if lang_code == "yue":
        cands = [it for it in items
                 if it.surface and (it.meaning_en or it.meaning_cn)]
        rnd.shuffle(cands)
        for it in cands[:MAX_QUESTIONS]:
            gloss = (it.meaning_en or it.meaning_cn or "").strip()
            if not gloss:
                continue
            others = _dedup([o.surface for o in items
                             if o.surface and o.surface != it.surface])
            picks = _shuffled(others[:N_OPTIONS - 1], rnd)[:N_OPTIONS - 1]
            opts = _shuffled([it.surface] + picks, rnd)
            out.append({
                "kind": "vocabulary",
                "prompt_en": f"Which is the Cantonese for: “{gloss}”?",
                "prompt_zh": f"下列哪个是「{gloss}」的粤语说法？",
                "options": json.dumps([{"en": o, "zh": meaning_of_word.get(o, "")}
                                       for o in opts], ensure_ascii=False),
                "answer_index": opts.index(it.surface),
                "explanation_en": f"“{it.surface}” means “{gloss}”.",
                "explanation_zh": f"「{it.surface}」的意思是「{gloss}」。",
                "content_id": it.id,
            })
        return out

    # 2) 英语：先出 cloze（最可靠的英文题型）
    for it in vocab:
        if len(out) >= MAX_QUESTIONS:
            break
        blanked = _cloze_blank(it.surface, it.example_en)
        if not blanked:
            continue
        others = [w for w in word_pool if w.lower() != it.surface.lower()]
        if len(others) < N_OPTIONS - 1:
            continue
        picks = _shuffled(others, rnd)[:N_OPTIONS - 1]
        opts = _shuffled([it.surface] + picks, rnd)
        out.append({
            "kind": "vocabulary",
            "prompt_en": blanked,
            "prompt_zh": f"补全句子：{it.example_cn or it.meaning_cn}",
            "options": json.dumps([{"en": o, "zh": meaning_of_word.get(o, "")}
                                   for o in opts], ensure_ascii=False),
            "answer_index": opts.index(it.surface),
            "explanation_en": it.example_en,
            "explanation_zh": it.example_cn or it.meaning_cn,
            "content_id": it.id,
        })

    # 3) 再补「单词 → 英文释义」（只有 meaning_en 有值才出，绝不拿中文顶替）
    if len(out) < MAX_QUESTIONS:
        cands = [it for it in items if it.meaning_en and it.surface]
        rnd.shuffle(cands)
        pool_en = _dedup([it.meaning_en for it in cands])
        for it in cands:
            if len(out) >= MAX_QUESTIONS:
                break
            others = [m for m in pool_en if m != it.meaning_en]
            if len(others) < N_OPTIONS - 1:
                continue
            picks = _shuffled(others, rnd)[:N_OPTIONS - 1]
            opts = _shuffled([it.meaning_en] + picks, rnd)
            out.append({
                "kind": it.kind or "vocabulary",
                "prompt_en": f"What does “{it.surface}” mean?",
                "prompt_zh": f"“{it.surface}” 的意思是？",
                "options": json.dumps([{"en": o, "zh": ""} for o in opts],
                                      ensure_ascii=False),
                "answer_index": opts.index(it.meaning_en),
                "explanation_en": it.example_en,
                "explanation_zh": it.example_cn or it.meaning_cn,
                "content_id": it.id,
            })

    return out[:MAX_QUESTIONS]


def unit_items(unit: Unit) -> list:
    """取出该 Unit 下的全部学习内容。"""
    return (ContentItem.query
            .join(Lesson, ContentItem.lesson_id == Lesson.id)
            .filter(Lesson.unit_id == unit.id)
            .all())


def ensure_quiz(unit: Unit, lang_code: str = "en", force: bool = False) -> Quiz:
    """为一个 Unit 幂等生成（或重建）单元测验。

    内容补齐后调用 ``force=True`` 可让题目跟着更新；用户的作答记录不受影响。
    """
    quiz = Quiz.query.filter_by(unit_id=unit.id).first()
    if quiz and not force:
        return quiz

    if quiz:
        QuizQuestion.query.filter_by(quiz_id=quiz.id).delete()
        db.session.flush()
    else:
        quiz = Quiz(unit_id=unit.id,
                    title_zh=f"第 {unit.no} 单元测验",
                    title_en=f"Unit {unit.no} Test",
                    pass_score=PASS_SCORE)
        db.session.add(quiz)
        db.session.flush()

    items = unit_items(unit)
    questions = _build_questions(unit, items, lang_code)
    for i, q in enumerate(questions, start=1):
        db.session.add(QuizQuestion(quiz_id=quiz.id, no=i, **q))
    quiz.question_count = len(questions)
    db.session.commit()
    logger.info("单元测验已生成：unit=%s 题目=%s", unit.id, len(questions))
    return quiz


def quiz_view(quiz: Quiz) -> list[dict]:
    """把题目整理成模板好用的结构（选项解析为 [{en, zh}]，并附上下标）。"""
    out = []
    for q in quiz.questions:
        try:
            opts = json.loads(q.options or "[]")
        except (TypeError, ValueError):
            opts = []
        out.append({
            "id": q.id, "no": q.no, "kind": q.kind,
            "prompt_en": q.prompt_en, "prompt_zh": q.prompt_zh,
            "options": opts,
            "answer_index": q.answer_index,
            "explanation_en": q.explanation_en,
            "explanation_zh": q.explanation_zh,
        })
    return out


# --------------------------------------------------------------------------
# 判分 / 记录 / 解锁联动
# --------------------------------------------------------------------------
def _next_unit(unit: Unit):
    """同课程内的下一个单元；已是最后一个则取下一门课程的第一个单元。"""
    nxt = Unit.query.filter_by(course_id=unit.course_id, no=unit.no + 1).first()
    if nxt:
        return nxt
    course = Course.query.get(unit.course_id)
    if not course:
        return None
    following = (Course.query
                 .filter(Course.language_code == course.language_code,
                         Course.sort > course.sort)
                 .order_by(Course.sort.asc()).first())
    if not following:
        return None
    return Unit.query.filter_by(course_id=following.id, no=1).first()


def _touch_progress(user_id: int, unit_id: int, **kw) -> UnitProgress:
    prog = UnitProgress.query.filter_by(user_id=user_id, unit_id=unit_id).first()
    if not prog:
        prog = UnitProgress(user_id=user_id, unit_id=unit_id)
        db.session.add(prog)
    for k, v in kw.items():
        setattr(prog, k, v)
    prog.updated_at = _utcnow()
    return prog


def grade(user_id: int, unit: Unit, answers: dict,
          started_at=None) -> UnitTestAttempt:
    """判分并存档；同时维护单元掌握状态与解锁联动。"""
    quiz = ensure_quiz(unit, _lang_of(unit))
    questions = list(quiz.questions)

    score = 0
    mistakes = []
    for q in questions:
        raw = answers.get(str(q.id), answers.get(q.id))
        try:
            given_i = int(raw)
        except (TypeError, ValueError):
            given_i = -1
        if given_i == q.answer_index:
            score += 1
            continue
        try:
            opts = json.loads(q.options or "[]")
        except (TypeError, ValueError):
            opts = []
        given_txt = opts[given_i]["en"] if 0 <= given_i < len(opts) else ""
        right_txt = opts[q.answer_index]["en"] if 0 <= q.answer_index < len(opts) else ""
        mistakes.append({
            "question_id": q.id,
            "kind": q.kind,
            "prompt": q.prompt_en,
            "given": given_txt,
            "correct": right_txt,
        })

    total = len(questions)
    accuracy = round(score * 100 / total) if total else 0

    counts: dict[str, int] = {}
    for m in mistakes:
        counts[m["kind"]] = counts.get(m["kind"], 0) + 1
    weak_areas = sorted(counts, key=lambda k: counts[k], reverse=True)

    duration = 0
    if started_at is not None:
        try:
            duration = max(0, int((_utcnow() - started_at).total_seconds()))
        except Exception:  # pragma: no cover
            duration = 0

    passed = bool(total) and accuracy >= quiz.pass_score

    attempt = UnitTestAttempt(
        user_id=user_id, unit_id=unit.id, quiz_id=quiz.id,
        score=score, total=total, accuracy=accuracy,
        duration_sec=duration,
        mistakes=json.dumps(mistakes, ensure_ascii=False),
        weak_areas=json.dumps(weak_areas, ensure_ascii=False),
        passed=passed, completed_at=_utcnow(),
    )
    db.session.add(attempt)

    # ── 掌握状态 + 解锁联动 ──────────────────────────────────────────
    prog = _touch_progress(user_id, unit.id)
    prog.attempts = (prog.attempts or 0) + 1
    prog.best_score = max(prog.best_score or 0, accuracy)
    if passed:
        prog.status = "mastered"
        prog.passed_at = _utcnow()
        nxt = _next_unit(unit)
        if nxt:
            nprog = _touch_progress(user_id, nxt.id)
            if nprog.status == "locked":
                nprog.status = "available"

    db.session.commit()
    return attempt


def _lang_of(unit: Unit) -> str:
    course = Course.query.get(unit.course_id)
    return (course.language_code if course else "en") or "en"


def unit_state(user_id: int, unit: Unit) -> dict:
    """该用户在此单元的状态：是否掌握 / 最佳成绩 / 尝试次数。"""
    prog = UnitProgress.query.filter_by(user_id=user_id, unit_id=unit.id).first()
    return {
        "status": (prog.status if prog else "available"),
        "best_score": (prog.best_score if prog else 0),
        "attempts": (prog.attempts if prog else 0),
        "mastered": bool(prog and prog.status == "mastered"),
    }


def latest_attempt(user_id: int, unit_id: int):
    return (UnitTestAttempt.query
            .filter_by(user_id=user_id, unit_id=unit_id)
            .order_by(UnitTestAttempt.completed_at.desc()).first())


def attempt_view(attempt: UnitTestAttempt) -> dict:
    try:
        mistakes = json.loads(attempt.mistakes or "[]")
    except (TypeError, ValueError):
        mistakes = []
    try:
        weak = json.loads(attempt.weak_areas or "[]")
    except (TypeError, ValueError):
        weak = []
    return {
        "id": attempt.id, "score": attempt.score, "total": attempt.total,
        "accuracy": attempt.accuracy, "duration_sec": attempt.duration_sec,
        "mistakes": mistakes, "weak_areas": weak,
        "passed": attempt.passed,
        "completed_at": attempt.completed_at,
    }


def weak_area_items(user_id: int, unit: Unit, limit: int = 8) -> list:
    """针对性练习（Targeted Practice）：错题涉及的内容项，用于重测前补强。"""
    attempt = latest_attempt(user_id, unit.id)
    if not attempt:
        return []
    try:
        weak = json.loads(attempt.weak_areas or "[]")
    except (TypeError, ValueError):
        weak = []
    items = unit_items(unit)
    if not weak:
        return items[:limit]
    hit = [it for it in items if (it.kind in weak)]
    return (hit or items)[:limit]
