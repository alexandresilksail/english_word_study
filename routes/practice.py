"""练习中心（Practice Hub）：6 种练习题型。

- Multiple Choice / Listening / Spelling / Cloze：复用既有 /api/quiz/* 测验引擎
  （这些题型在服务端已有完整出题-判分逻辑），本页只做入口与参数预设。
- Matching / Sentence Building / Speaking：基于 /api/practice/words 拉取的词库，
  在浏览器内纯前端完成；客户端按当前 UI 语言选择「英文-英文」或「英文-中文」配对，
  保证 English 版面不出现中文。

English 版面零中文原则同样适用于练习区：前端读取 <html data-lang> 决定文案语言。
"""
from __future__ import annotations

from flask import Blueprint, jsonify, render_template, request
from flask_login import current_user, login_required

from extensions import db
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
