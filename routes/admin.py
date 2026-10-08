"""简易管理员后台：用户数量 / 用户列表 / 词库数量 / 学习次数 / 测试次数。"""
from __future__ import annotations

from datetime import datetime, timedelta

from flask import Blueprint, abort, jsonify, render_template, request
from flask_login import current_user
from sqlalchemy import func

from auth import admin_required
from extensions import db
from models import (Favorite, StudyRecord, TestRecord, User, UserWordProgress, Word,
                    WrongAnswer)
from services import today_start

admin_bp = Blueprint("admin", __name__, url_prefix="/admin")


def _today():
    return datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)


@admin_bp.route("/")
@admin_required
def console():
    total_users = User.query.count()
    admins = User.query.filter_by(is_admin=True).count()
    total_words = Word.query.count()
    study_count = StudyRecord.query.count()
    answer_count = StudyRecord.query.filter_by(action="answer").count()
    test_count = TestRecord.query.count()
    fav_count = Favorite.query.count()
    wrong_count = WrongAnswer.query.count()
    mastered = UserWordProgress.query.filter_by(status="mastered").count()

    today = _today()
    new_users_today = User.query.filter(User.created_at >= today).count()
    active_today = db.session.query(func.count(func.distinct(StudyRecord.user_id))).filter(
        StudyRecord.created_at >= today).scalar() or 0
    answers_today = StudyRecord.query.filter_by(action="answer").filter(
        StudyRecord.created_at >= today).count()
    tests_today = TestRecord.query.filter(TestRecord.created_at >= today).count()

    recent_users = User.query.order_by(User.created_at.desc()).limit(20).all()

    # 近 7 天注册趋势
    days = []
    for i in range(6, -1, -1):
        d = today - timedelta(days=i)
        cnt = User.query.filter(User.created_at >= d,
                                User.created_at < d + timedelta(days=1)).count()
        days.append({"day": d.strftime("%m-%d"), "count": cnt})

    return render_template(
        "admin.html",
        stats=dict(total_users=total_users, admins=admins, total_words=total_words,
                   study_count=study_count, answer_count=answer_count,
                   test_count=test_count, fav_count=fav_count, wrong_count=wrong_count,
                   mastered=mastered, new_users_today=new_users_today,
                   active_today=active_today, answers_today=answers_today,
                   tests_today=tests_today),
        recent_users=recent_users, days=days,
    )


@admin_bp.route("/users")
@admin_required
def users():
    page = request.args.get("page", 1, type=int) or 1
    per = 20
    total = User.query.count()
    pages = max(1, (total + per - 1) // per)
    page = min(max(page, 1), pages)
    rows = (User.query.order_by(User.created_at.desc())
            .offset((page - 1) * per).limit(per).all())

    # V5.9 性能：避免「逐用户查一次」的 N+1。改为按当前页 user_id 批量聚合，
    # 用 7 条查询代替 7×N 条，用户规模增长时不再线性放大。
    ids = [u.id for u in rows]
    _answers = dict(
        db.session.query(StudyRecord.user_id, func.count(StudyRecord.id))
        .filter(StudyRecord.user_id.in_(ids), StudyRecord.action == "answer")
        .group_by(StudyRecord.user_id).all())
    _correct = dict(
        db.session.query(StudyRecord.user_id, func.count(StudyRecord.id))
        .filter(StudyRecord.user_id.in_(ids), StudyRecord.action == "answer",
                StudyRecord.is_correct.is_(True))
        .group_by(StudyRecord.user_id).all())
    _tests = dict(
        db.session.query(TestRecord.user_id, func.count(TestRecord.id))
        .filter(TestRecord.user_id.in_(ids)).group_by(TestRecord.user_id).all())
    _favs = dict(
        db.session.query(Favorite.user_id, func.count(Favorite.id))
        .filter(Favorite.user_id.in_(ids)).group_by(Favorite.user_id).all())
    _mastered = dict(
        db.session.query(UserWordProgress.user_id, func.count(UserWordProgress.id))
        .filter(UserWordProgress.user_id.in_(ids), UserWordProgress.status == "mastered")
        .group_by(UserWordProgress.user_id).all())
    _wrong = dict(
        db.session.query(WrongAnswer.user_id, func.count(WrongAnswer.id))
        .filter(WrongAnswer.user_id.in_(ids)).group_by(WrongAnswer.user_id).all())

    data = []
    for u in rows:
        answers = _answers.get(u.id, 0)
        correct = _correct.get(u.id, 0)
        data.append({
            "id": u.id, "email": u.email, "username": u.username,
            "is_admin": u.is_admin, "created_at": u.created_at,
            "last_login_at": u.last_login_at, "login_count": u.login_count or 0,
            "answers": answers, "accuracy": round(correct * 100 / answers) if answers else 0,
            "tests": _tests.get(u.id, 0),
            "favs": _favs.get(u.id, 0),
            "mastered": _mastered.get(u.id, 0),
            "wrong": _wrong.get(u.id, 0),
        })
    return render_template("admin_users.html", users=data, page=page, pages=pages, total=total)


@admin_bp.route("/api/stats")
@admin_required
def api_stats():
    return jsonify(
        users=User.query.count(),
        words=Word.query.count(),
        study_records=StudyRecord.query.count(),
        answers=StudyRecord.query.filter_by(action="answer").count(),
        tests=TestRecord.query.count(),
    )
