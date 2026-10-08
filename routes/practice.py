"""练习中心（Practice Hub）：6 种练习题型。

- Multiple Choice / Listening / Spelling / Cloze：复用既有 /api/quiz/* 测验引擎
  （这些题型在服务端已有完整出题-判分逻辑），本页只做入口与参数预设。
- Matching / Sentence Building / Speaking：基于 /api/practice/words 拉取的词库，
  在浏览器内纯前端完成；客户端按当前 UI 语言选择「英文-英文」或「英文-中文」配对，
  保证 English 版面不出现中文。

English 版面零中文原则同样适用于练习区：前端读取 <html data-lang> 决定文案语言。
"""
from __future__ import annotations

from flask import Blueprint, jsonify, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from api_response import fail, ok
from extensions import db
from mastery_service import record_practice
from models import UserWordProgress, Word

practice_bp = Blueprint("practice", __name__, url_prefix="/practice")


# 题型元信息（标题/简介均中英双语，由模板 pick 输出）
PRACTICE_TYPES = [
    ("multiple_choice", "Multiple Choice", "选择题",
     "Choose the correct meaning", "看英文单词，选出正确释义", "quiz"),
    ("listening", "Listening", "听力",
     "Listen and pick the word", "听发音，选出正确单词", "quiz"),
    ("spelling", "Spelling", "拼写",
     "Type the word you hear", "听发音，拼写出单词", "quiz"),
    ("cloze", "Fill in the Blank", "填空",
     "Complete the sentence", "读例句，补全缺失的单词", "local"),
    ("matching", "Matching", "配对",
     "Match words with meanings", "把单词与释义快速配对", "local"),
    ("sentence", "Sentence Building", "造句",
     "Reorder words into a sentence", "把打乱的单词重组成完整句子", "local"),
    ("speaking", "Speaking", "口语",
     "Shadow the word, get scored", "跟读单词，系统打分", "local"),
]


@practice_bp.route("", endpoint="index")
@practice_bp.route("/", endpoint="index_slash")
@login_required
def index():
    return render_template("practice.html", types=PRACTICE_TYPES)


@practice_bp.route("/submit", methods=["POST"])
def submit():
    """练习结果上报（V5.5）：写入 ReviewItem（Leitner）+ ContentMastery（计数 / 弱项）。

    请求形态兼容两种：
    * JSON（fetch / 小程序）：``{"content_id":N,"correct":bool,"kind":..,"mode":..,"given":..,"expected":..}``
      或批量 ``{"items":[{...},{...}]}`` → 返回 ``{ok,data}`` JSON；
    * 表单（服务器渲染，渐进增强）：``content_id / correct / kind / csrf_token`` → 302 跳回来源页。

    未登录统一返回 401 JSON（与 REST API 一致，便于多端复用）。
    """
    if not current_user.is_authenticated:
        return fail("请先登录", code="unauthorized", status=401)

    is_json = request.is_json
    body = request.get_json(silent=True) if is_json else None
    # fetch + FormData 走表单编码但带 X-Requested-With，也按 JSON 响应
    is_ajax = (request.headers.get("X-Requested-With") == "XMLHttpRequest") or is_json
    if body is None:
        # 表单：用 request.form 逐个字段取值
        body = request.form

    items = body.get("items") if isinstance(body, dict) else None
    if items:
        results = [_record_one(it) for it in items]
        payload = {"count": len(results), "results": results}
        if is_ajax:
            return ok(payload)
        return redirect(request.referrer or url_for("main.courses"))

    # 单条提交：缺 content_id 属于请求错误，返回 422 JSON（顶层 ok=False）
    res = _record_one(body)
    if not res.get("ok"):
        return fail(res.get("error", "bad request"), code="bad_request", status=422)
    if is_ajax:
        return ok(res)
    # 表单非 AJAX：跳回来源页（保持上下文）
    return redirect(request.referrer or url_for("main.courses"))


def _record_one(it) -> dict:
    """处理单条练习结果；兼容 dict（JSON）与 ImmutableMultiDict（表单）。"""
    if isinstance(it, dict):
        cid = it.get("content_id") or it.get("contentId")
        correct = bool(it.get("correct"))
        kind = it.get("kind")
        mode = it.get("mode")
        given = it.get("given")
        expected = it.get("expected")
    else:
        cid = (it or request.form).get("content_id", type=int)
        correct = ((it or request.form).get("correct") or "0") == "1"
        kind = (it or request.form).get("kind")
        mode = (it or request.form).get("mode")
        given = (it or request.form).get("given")
        expected = (it or request.form).get("expected")

    if not cid:
        return {"ok": False, "error": "missing content_id"}
    try:
        cid = int(cid)
    except (TypeError, ValueError):
        return {"ok": False, "error": "invalid content_id"}

    m = record_practice(current_user.id, cid, correct,
                        kind=kind, mode=mode, given=given, expected=expected)
    return {
        "ok": True,
        "content_id": cid,
        "level": m.level,
        "correct_count": m.correct_count,
        "wrong_count": m.wrong_count,
        "streak": m.streak,
        "weak": m.weak,
        "next_review_at": m.next_review_at.isoformat() if m.next_review_at else None,
    }


@practice_bp.route("/api/words")
@login_required
def api_words():
    """给前端配对/造句游戏提供词库。

    返回每个单词的 word / phonetic / pos / meaning_cn / example_en / audio，
    前端按当前 UI 语言决定展示哪一侧（en 模式只用 example_en，绝不显示中文）。
    """
    try:
        n = max(4, min(int(request.args.get("n") or 8), 30))
    except (TypeError, ValueError):
        n = 8
    scope = (request.args.get("scope") or "all").strip()

    q = Word.query
    if scope == "mastered":
        q = q.join(UserWordProgress).filter(
            UserWordProgress.user_id == current_user.id,
            UserWordProgress.status == "mastered")
    elif scope == "learning":
        q = q.join(UserWordProgress).filter(
            UserWordProgress.user_id == current_user.id,
            UserWordProgress.status == "learning")

    words = q.order_by(db.func.random()).limit(n * 4).all()
    out = []
    for w in words:
        if len(out) >= n:
            break
        out.append({
            "word": w.word,
            "phonetic": w.phonetic_uk,
            "pos": w.pos,
            "meaning": w.meaning_cn,        # 仅在非 en 模式前端展示
            "example": w.example_en,        # en 模式前端用此句做配对/填空
            "audio": w.audio,
        })
    return jsonify(ok=True, words=out)
