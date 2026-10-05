"""测试中心：英文→中文 / 中文→英文 / 听音 / 拼写 四种模式 + JSON API。

答题状态保存在 Flask 会话 Cookie 中（而非进程内存），
因此 Gunicorn 多 worker 下也不会丢失或串味。
"""
from __future__ import annotations

import random
from datetime import datetime

from flask import Blueprint, abort, jsonify, render_template, request, session
from flask_login import current_user, login_required

from extensions import db
from forms import EmptyForm
from models import TEST_MODE_LABELS, TestRecord, Word
from services import build_question, judge, record_answer

quiz_bp = Blueprint("quiz", __name__)

MAX_SIZE = 50
SIZES = [10, 20, 30, 50]
SCOPES = {
    "all": "全部单词", "new": "未学习", "learning": "学习中",
    "mastered": "已掌握", "favorites": "我的收藏", "wrong": "错题本",
}


def _quiz_state():
    return session.get("quiz")


def _require_quiz():
    state = _quiz_state()
    if not state or not state.get("ids"):
        return None
    return state


def _expected_text(word: Word, mode: str) -> str:
    """某一模式下该题的正确答案文本（不依赖选项，word 表直接可得）。"""
    return word.meaning_cn if mode == "choice" else word.word


def _saved_correct_idx(state: dict, idx: int):
    """取出出题时保存的「正确选项下标」。

    出题与判题是两次请求，若判题时重新生成随机选项，用户看到的下标就会错位。
    因此出题时把正确项下标按题号存好，判题时直接读取——每题一个键，
    第 0/1/2…题彼此不会覆盖。
    """
    try:
        saved = (state.get("answers") or {}).get(str(idx))
        return None if saved is None else int(saved)
    except (TypeError, ValueError):
        return None


def _as_option_index(answer) -> int | None:
    """把前端提交内容解析成选项下标；提交的是文本时返回 None。"""
    if isinstance(answer, bool):
        return None
    if isinstance(answer, int):
        return answer
    text = str(answer).strip()
    if text.lstrip("-").isdigit():
        try:
            return int(text)
        except ValueError:
            return None
    return None


@quiz_bp.route("/test")
@login_required
def test_page():
    preset_mode = (request.args.get("mode") or "").strip()
    preset_scope = (request.args.get("scope") or "").strip()
    return render_template("test.html", sizes=SIZES, scopes=SCOPES,
                           labels=TEST_MODE_LABELS, form=EmptyForm(),
                           preset_mode=preset_mode, preset_scope=preset_scope)


@quiz_bp.route("/api/quiz/start", methods=["POST"])
@login_required
def quiz_start():
    body = request.get_json(silent=True) or {}
    mode = (body.get("mode") or "choice").strip()
    scope = (body.get("scope") or "all").strip()
    if mode not in TEST_MODE_LABELS:
        mode = "choice"
    if scope not in SCOPES:
        scope = "all"
    try:
        size = int(body.get("size") or 10)
    except (TypeError, ValueError):
        size = 10
    size = max(1, min(size, MAX_SIZE))

    from services import _pick_word_ids
    ids = _pick_word_ids(current_user.id, scope, size)
    if not ids:
        return jsonify(ok=False, error="该范围内没有可用的单词，请更换范围"), 400

    session["quiz"] = {
        "mode": mode, "ids": ids, "total": len(ids),
        "answered": 0, "started": datetime.now().timestamp(),
        # seed 保证刷新/重复请求时选项不变；answers/snap 保存每题判题状态
        "seed": random.randrange(1, 2 ** 31),
        "answers": {}, "snap": None,
    }
    session.modified = True
    return jsonify(ok=True, mode=mode, total=len(ids))


@quiz_bp.route("/api/quiz/item")
@login_required
def quiz_item():
    state = _require_quiz()
    if not state:
        return jsonify(ok=False, error="测试未开始"), 400
    try:
        idx = int(request.args.get("i") or 0)
    except ValueError:
        idx = 0
    if idx < 0 or idx >= state["total"]:
        return jsonify(ok=False, error="题目序号超出范围"), 400

    word = Word.query.get(state["ids"][idx])
    if not word:
        return jsonify(ok=False, error="题目数据异常"), 500
    mode = state["mode"]
    q = build_question(word, mode, state.get("seed"))
    q["i"] = idx
    q["total"] = state["total"]

    # ---- 保存本题实际使用的判题状态（判题时不再重新生成随机选项）----
    opts = list(q.get("options") or [])
    answers = dict(state.get("answers") or {})
    if opts and q.get("answer") in opts:
        answers[str(idx)] = opts.index(q["answer"])   # 按题号存，互不覆盖
    elif opts:
        answers[str(idx)] = -1                        # 异常：选项里没有正确答案
    else:
        answers[str(idx)] = -1                        # spell 模式无选项
    state["answers"] = answers
    # 当前题选项快照，用于判题后回填"用户当时看到的那一项"文本
    state["snap"] = {"i": idx, "options": opts, "mode": mode}
    session.modified = True

    # 不要把答案直接暴露给前端
    q.pop("answer", None)
    return jsonify(ok=True, q=q)


@quiz_bp.route("/api/quiz/answer", methods=["POST"])
@login_required
def quiz_answer():
    state = _require_quiz()
    if not state:
        return jsonify(ok=False, error="测试未开始"), 400
    body = request.get_json(silent=True) or {}
    try:
        idx = int(body.get("i") or 0)
    except (TypeError, ValueError):
        idx = 0
    if idx < 0 or idx >= state["total"]:
        return jsonify(ok=False, error="题目序号超出范围"), 400

    answer = body.get("answer")
    if answer is None:
        answer = ""
    if not isinstance(answer, str):
        answer = str(answer)

    word = Word.query.get(state["ids"][idx])
    if not word:
        return jsonify(ok=False, error="题目数据异常"), 500

    mode = state["mode"]
    correct_text = _expected_text(word, mode)

    if mode == "spell":
        # 拼写题：用户直接输入英文单词，没有选项，不存在错位问题
        user_value = answer.strip()
        correct = judge({"mode": mode, "answer": correct_text}, user_value)
    else:
        # ---- 选择题：必须用「用户当时看到的原始题目状态」判题 ----
        snap = state.get("snap") or {}
        if snap.get("i") == idx and isinstance(snap.get("options"), list):
            opts = list(snap["options"])           # 出题时保存的快照，不重新生成
        else:
            # 快照不是本题（多标签页答题 / 旧会话）：用与出题相同的 seed 确定性复现
            rebuilt = build_question(word, mode, state.get("seed"))
            opts = list(rebuilt.get("options") or [])

        opt_idx = _as_option_index(answer)
        if opt_idx is None:
            # 提交的是选项文本（兼容手工调用），与正确答案文本直接比对
            user_value = str(answer).strip()
            correct = judge({"mode": mode, "answer": correct_text}, user_value)
        else:
            user_value = opts[opt_idx] if 0 <= opt_idx < len(opts) else ""
            saved_idx = _saved_correct_idx(state, idx)
            if saved_idx is not None and saved_idx >= 0:
                # 判题依据来自出题时保存的状态
                correct = (opt_idx == saved_idx)
            else:
                # 没有保存状态兜底：退化为文本比对
                correct = judge({"mode": mode, "answer": correct_text}, user_value)

    record_answer(current_user.id, word.id, correct, mode)

    return jsonify(
        ok=True, correct=correct, expected=correct_text,
        user_answer=user_value,
        phonetic=word.phonetic_uk, meaning=word.meaning_cn,
        example_en=word.example_en, example_cn=word.example_cn,
        audio=word.audio, word=word.word, mode=mode,
    )


@quiz_bp.route("/api/quiz/finish", methods=["POST"])
@login_required
def quiz_finish():
    state = _require_quiz()
    if not state:
        return jsonify(ok=False, error="测试未开始"), 400

    from services import _fmt_dt
    from sqlalchemy import func as _f
    from models import StudyRecord

    started = state.get("started") or datetime.now().timestamp()
    duration = max(1, int(datetime.now().timestamp() - started))

    # 统计本次测试的作答情况
    rows = (StudyRecord.query.filter_by(user_id=current_user.id, action="answer")
            .filter(StudyRecord.created_at >= datetime.fromtimestamp(started - 1))
            .all())
    listed = dict.fromkeys(state["ids"])
    mine = [r for r in rows if r.word_id in listed]
    score = sum(1 for r in mine if r.is_correct)
    total = state["total"]

    rec = TestRecord(user_id=current_user.id, mode=state["mode"], total=total,
                     score=score, duration_sec=duration)
    db.session.add(rec)
    db.session.commit()
    session.pop("quiz", None)
    session.modified = True

    pct = round(score * 100 / total) if total else 0
    comment = ("outstanding" if pct >= 90 else "good" if pct >= 70 else
               "pass" if pct >= 50 else "retry")
    return jsonify(ok=True, total=total, score=score, percent=pct,
                   duration=duration, mode=state["mode"], comment=comment, record_id=rec.id)


@quiz_bp.route("/api/audio/<int:word_id>")
@login_required
def api_audio(word_id: int):
    """返回单词音频地址（前端用本地 MP3 播放，不调用任何在线 TTS）。"""
    word = Word.query.get_or_404(word_id)
    return jsonify(ok=True, word=word.word, audio=word.audio)
