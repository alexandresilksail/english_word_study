"""单词相关页面：今日学习、A-Z 浏览、搜索、详情、收藏、错题本、学习进度。"""
from __future__ import annotations

from flask import Blueprint, abort, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required
from sqlalchemy import or_

from extensions import db
from forms import EmptyForm
from models import (Favorite, StudyRecord, TestRecord, UserWordProgress, Word,
                    WrongAnswer, utcnow)
from services import (STATUS_LABELS, build_word_view, daily_trend, dashboard_stats,
                      learning_streak, record_answer, today_start, touch_word,
                      unlocked_badges)

words_bp = Blueprint("words", __name__)

LETTERS = [chr(c) for c in range(ord("A"), ord("Z") + 1)]
PAGE_SIZE = 20
SCOPES = {
    "all": "全部单词", "new": "未学习", "learning": "学习中",
    "mastered": "已掌握", "favorites": "我的收藏", "wrong": "错题本",
}


def _visible_to_user():
    return current_user.is_authenticated


@words_bp.route("/learn")
@login_required
def learn():
    uid = current_user.id
    page = request.args.get("page", 1, type=int) or 1
    scope = request.args.get("scope", "all")
    letter = (request.args.get("letter") or "").lower()
    cefr = (request.args.get("cefr") or "").upper()
    unit = request.args.get("unit", type=int)

    progress_ids = {
        p.word_id: p for p in UserWordProgress.query.filter_by(user_id=uid).all()
    }
    fav_ids = {f.word_id for f in Favorite.query.filter_by(user_id=uid).all()}
    wrong_ids = {w.word_id for w in WrongAnswer.query.filter_by(user_id=uid).all()}

    # V5：按 CEFR 等级 / 单元过滤（来自学习路径页的入口）
    if cefr or unit:
        from models import WordMeta
        mq = WordMeta.query
        if cefr:
            mq = mq.filter_by(cefr_level=cefr)
        if unit:
            mq = mq.filter_by(unit_no=unit)
        meta_ids = [m.word_id for m in mq.all()]
    else:
        meta_ids = None

    q = Word.query
    if meta_ids is not None:
        q = q.filter(Word.id.in_(meta_ids))
    if scope == "favorites":
        words = [Word.query.get(i) for i in fav_ids]
        words = sorted([w for w in words if w], key=lambda x: x.word)
        if meta_ids is not None:
            words = [w for w in words if w.id in set(meta_ids)]
    elif scope == "wrong":
        q = q.join(WrongAnswer, WrongAnswer.word_id == Word.id).filter(WrongAnswer.user_id == uid)
        words = q.order_by(Word.word).all()
    else:
        if scope == "new":
            studied = db.session.query(UserWordProgress.word_id).filter_by(user_id=uid)
            q = q.filter(~Word.id.in_(studied))
        elif scope in ("learning", "mastered"):
            ids = [k for k, v in progress_ids.items() if v.status == scope]
            q = q.filter(Word.id.in_(ids)) if ids else q.filter(Word.id == -1)
        if letter:
            q = q.filter(Word.initial == letter)
        words = q.order_by(Word.word).all()

    total = len(words)
    pages = max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)
    page = min(max(page, 1), pages)
    rows = words[(page - 1) * PAGE_SIZE: page * PAGE_SIZE]
    items = [build_word_view(w, uid) for w in rows]

    if items:
        for w in items:
            touch_word(uid, w["id"], "view")   # 浏览即计入学习

    return render_template("learn.html", words=items, total=total, page=page,
                           pages=pages, scope=scope, scopes=SCOPES, letter=letter,
                           letters=LETTERS, form=EmptyForm(), labels=STATUS_LABELS)


@words_bp.route("/words")
@login_required
def browse():
    uid = current_user.id
    q = (request.args.get("q") or "").strip()
    letter = (request.args.get("letter") or "").lower()
    page = request.args.get("page", 1, type=int) or 1

    query = Word.query
    if q:
        like = f"%{q}%"
        query = query.filter(or_(Word.word.like(like), Word.meaning_cn.like(like),
                                 Word.example_en.like(like)))
    elif letter:
        query = query.filter(Word.initial == letter)

    counts = {r[0]: r[1] for r in db.session.query(Word.initial, db.func.count(Word.id))
              .group_by(Word.initial).all()}
    total = query.count()
    pages = max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)
    page = min(max(page, 1), pages)
    rows = query.order_by(Word.word).offset((page - 1) * PAGE_SIZE).limit(PAGE_SIZE).all()
    items = [build_word_view(w, uid) for w in rows]
    return render_template("words.html", words=items, total=total, page=page, pages=pages,
                           q=q, letter=letter, letters=LETTERS, counts=counts, form=EmptyForm())


@words_bp.route("/word/<int:word_id>")
@login_required
def detail(word_id: int):
    uid = current_user.id
    word = Word.query.get_or_404(word_id)
    touch_word(uid, word.id, "view")
    item = build_word_view(word, uid)
    return render_template("detail.html", w=item, form=EmptyForm())


@words_bp.route("/favorites")
@login_required
def favorites():
    uid = current_user.id
    rows = (Favorite.query.filter_by(user_id=uid)
            .order_by(Favorite.created_at.desc()).all())
    items = [build_word_view(f.word, uid) for f in rows]
    return render_template("favorites.html", words=items, form=EmptyForm())


@words_bp.route("/wrong")
@login_required
def wrong():
    uid = current_user.id
    rows = (WrongAnswer.query.filter_by(user_id=uid)
            .order_by(WrongAnswer.last_wrong_at.desc()).all())
    items = [build_word_view(w.word, uid) for w in rows]
    for it in items:
        it["wrong_count"] = next((w.wrong_count for w in rows if w.word_id == it["id"]), 0)
    return render_template("wrong.html", words=items, form=EmptyForm())


@words_bp.route("/progress")
@login_required
def progress():
    uid = current_user.id
    stats = dashboard_stats(uid)
    stats["streak"] = learning_streak(uid)
    trend = daily_trend(uid, 7)
    tests = (TestRecord.query.filter_by(user_id=uid)
             .order_by(TestRecord.created_at.desc()).limit(20).all())
    by_status = {
        r[0]: r[1] for r in db.session.query(UserWordProgress.status,
                                             db.func.count(UserWordProgress.id))
        .filter_by(user_id=uid).group_by(UserWordProgress.status).all()
    }
    history = (TestRecord.query.filter_by(user_id=uid)
               .order_by(TestRecord.created_at.desc()).all())
    return render_template("progress.html", stats=stats, trend=trend, tests=tests,
                           history=history, badges=unlocked_badges(stats),
                           by_status=by_status, labels=STATUS_LABELS)


@words_bp.route("/action/toggle-favorite", methods=["POST"])
@login_required
def toggle_favorite():
    """收藏 / 取消收藏（带 CSRF 校验）。"""
    uid = current_user.id
    word_id = request.form.get("word_id", type=int)
    if not word_id or not Word.query.get(word_id):
        abort(404)
    fav = Favorite.query.filter_by(user_id=uid, word_id=word_id).first()
    if fav:
        db.session.delete(fav)
        faved = False
    else:
        db.session.add(Favorite(user_id=uid, word_id=word_id))
        touch_word(uid, word_id, "favorite")
        faved = True
    db.session.commit()
    flash("已加入收藏 ⭐" if faved else "已取消收藏", "success")
    return redirect(request.referrer or url_for("words.browse"))


@words_bp.route("/action/remove-wrong", methods=["POST"])
@login_required
def remove_wrong():
    uid = current_user.id
    word_id = request.form.get("word_id", type=int)
    row = WrongAnswer.query.filter_by(user_id=uid, word_id=word_id).first()
    if row:
        db.session.delete(row)
        db.session.commit()
        flash("已移出错题本", "success")
    return redirect(request.referrer or url_for("words.wrong"))


@words_bp.route("/action/set-status", methods=["POST"])
@login_required
def set_status():
    """手动标记学习状态（学习中 / 已掌握 / 重置）。"""
    uid = current_user.id
    word_id = request.form.get("word_id", type=int)
    status = (request.form.get("status") or "").strip()
    if status not in STATUS_LABELS:
        abort(400)
    prog = UserWordProgress.query.filter_by(user_id=uid, word_id=word_id).first()
    if not prog:
        prog = UserWordProgress(user_id=uid, word_id=word_id, first_learned_at=utcnow())
        db.session.add(prog)
    prog.status = status
    prog.last_studied_at = utcnow()
    if status == "mastered" and not prog.mastered_at:
        prog.mastered_at = utcnow()
    if status == "new":
        prog.mastered_at = None
    db.session.commit()
    flash(f"已标记为「{STATUS_LABELS[status]}」", "success")
    return redirect(request.referrer or url_for("words.browse"))
