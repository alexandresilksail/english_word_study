"""首页、控制面板、个人资料、健康检查。"""
from __future__ import annotations

from flask import Blueprint, flash, jsonify, redirect, render_template, request, url_for
from flask_login import current_user, login_required
from sqlalchemy import func

from extensions import db
from forms import ChangePasswordForm, ProfileForm
from models import StudyRecord, TestRecord, User, Word
from services import (daily_trend, dashboard_stats, learning_streak, today_start,
                      unlocked_badges)

main_bp = Blueprint("main", __name__)


@main_bp.route("/")
def index():
    """未登录：产品落地页。已登录：直接进入学习主页。"""
    if current_user.is_authenticated:
        return redirect(url_for("main.dashboard"))
    total_words = Word.query.count()
    return render_template("index.html", total_words=total_words)


@main_bp.route("/dashboard")
@login_required
def dashboard():
    uid = current_user.id
    stats = dashboard_stats(uid)
    stats["streak"] = learning_streak(uid)
    trend = daily_trend(uid, 7)

    recent_tests = (TestRecord.query.filter_by(user_id=uid)
                    .order_by(TestRecord.created_at.desc()).limit(5).all())
    today_new = max(stats["total_words"] - stats["learned"], 0)

    return render_template("dashboard.html", stats=stats, trend=trend,
                           recent_tests=recent_tests, badges=unlocked_badges(stats),
                           today_new=today_new)


@main_bp.route("/profile", methods=["GET", "POST"])
@login_required
def profile():
    uid = current_user.id
    profile_form = ProfileForm(username=current_user.username)
    pwd_form = ChangePasswordForm()

    # 同页存在两个表单，用隐藏字段区分提交意图
    if request.method == "POST":
        kind = (request.form.get("form_kind") or "").strip()
        if kind == "profile" and profile_form.validate_on_submit():
            current_user.username = (profile_form.username.data or "").strip()
            db.session.commit()
            flash("用户名已更新 ✅", "success")
            return redirect(url_for("main.profile"))
        if kind == "password" and pwd_form.validate_on_submit():
            if not current_user.check_password(pwd_form.current_password.data):
                flash("当前密码不正确 ❌", "error")
            else:
                current_user.set_password(pwd_form.new_password.data)
                db.session.commit()
                flash("密码修改成功，下次登录请使用新密码 🔐", "success")
                return redirect(url_for("main.profile"))

    stats = dashboard_stats(uid)
    stats["streak"] = learning_streak(uid)
    joined_days = (today_start().date() - current_user.created_at.date()).days + 1

    recent = (StudyRecord.query.filter_by(user_id=uid, action="answer")
              .order_by(StudyRecord.created_at.desc()).limit(8).all())
    return render_template("profile.html", form=profile_form, pwd_form=pwd_form,
                           stats=stats, recent=recent, joined_days=joined_days,
                           badges=unlocked_badges(stats))


@main_bp.route("/healthz")
def healthz():
    """健康检查：Docker / Nginx / 监控探针使用。"""
    try:
        total_words = Word.query.count()
        users = db.session.query(func.count(User.id)).scalar() or 0
        return jsonify(status="ok", words=total_words, users=users)
    except Exception as exc:  # pragma: no cover
        return jsonify(status="error", detail=str(exc)), 500
