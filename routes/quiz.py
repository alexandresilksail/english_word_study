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
        # 出题与判题共用同一个 seed，保证选项集合/顺序完全可复现
        "seed": random.randrange(1, 2 ** 31),
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
    q = build_question(word, state["mode"], state.get("seed"))
    q["i"] = idx
    q["total"] = state["total"]
    # 不要把答案直接暴露给前端（除拼写模式需要校验输入，但仍不回传答案）
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

    # 用与出题时相同的 seed 重建题目，选项下标才对得上
    question = build_question(word, state["mode"], state.get("seed"))
    # 选择题：answer 是选项下标
    if question["mode"] != "spell":
        try:
            opt_idx = int(answer)
            user_value = question["options"][opt_idx] if 0 <= opt_idx < len(question["options"]) else ""
        except (TypeError, ValueError):
            user_value = str(answer).strip()
            if user_value.isdigit():
                n = int(user_value)
                user_value = question["options"][n] if 0 <= n < len(question["options"]) else ""
    else:
        user_value = answer.strip()

    correct = judge(question, user_value)
    record_answer(current_user.id, word.id, correct, question["mode"])

    return jsonify(
        ok=True, correct=correct, expected=question["answer"],
        user_answer=user_value,
        phonetic=word.phonetic_uk, meaning=word.meaning_cn,
        example_en=word.example_en, example_cn=word.example_cn,
        audio=word.audio, word=word.word, mode=question["mode"],
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
