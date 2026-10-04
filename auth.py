"""用户认证：注册 / 登录 / 退出 + 权限装饰器。

安全要点：
- 密码使用 Werkzeug pbkdf2:sha256 哈希，绝不明文保存
- 登录失败达到阈值后临时锁定账户
- 退出登录会清空 session 并记住我 cookie
"""
from __future__ import annotations

from datetime import timedelta
from functools import wraps

from flask import (Blueprint, abort, flash, redirect, render_template, request,
                   session, url_for)
from flask_login import current_user, login_required, login_user, logout_user

from extensions import db
from forms import LoginForm, RegistrationForm
from models import User, utcnow
from utils.ratelimit import clear_login_failures, register_login_failure

auth_bp = Blueprint("auth", __name__, url_prefix="")


def _safe_next(target: str | None) -> str | None:
    """防止开放重定向：只允许站内相对路径。"""
    if not target or not isinstance(target, str):
        return None
    target = target.strip()
    if target.startswith("//") or "://" in target or target.startswith("\\\\"):
        return None
    if not target.startswith("/") or target.startswith("/\\"):
        return None
    return target


@auth_bp.route("/register", methods=["GET", "POST"])
def register():
    if current_user.is_authenticated:
        return redirect(url_for("main.dashboard"))

    form = RegistrationForm()
    if form.validate_on_submit():
        email = (form.email.data or "").strip().lower()
        if User.query.filter_by(email=email).first():
            flash("该邮箱已被注册，请直接登录", "error")
            return render_template("register.html", form=form)

        user = User(email=email, username=(form.username.data or "").strip())
        user.set_password(form.password.data)
        db.session.add(user)
        db.session.commit()

        _ = session.get("_flashes")  # touch session ensure cookie exists
        flash("注册成功！已自动登录，开始背单词吧 🎉", "success")
        _login_now(user, remember=True)
        return redirect(url_for("main.dashboard"))

    return render_template("register.html", form=form)


@auth_bp.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for("main.dashboard"))

    form = LoginForm()
    if form.validate_on_submit():
        email = (form.email.data or "").strip().lower()
        user = User.query.filter_by(email=email).first()
        ok = False

        if user:
            remaining, wait_seconds = register_login_failure(user)
            if user.is_locked:
                flash(f"登录失败次数过多，请 {wait_seconds // 60 + 1} 分钟后再试", "error")
                return render_template("login.html", form=form)
            if user.check_password(form.password.data):
                ok = True

        if ok:
            clear_login_failures(user)
            _login_now(user, remember=bool(form.remember.data))
            flash(f"欢迎回来，{user.username}！", "success")
            return redirect(_safe_next(request.args.get("next")) or url_for("main.dashboard"))

        # 统一提示，避免暴露邮箱是否已注册
        remaining = 0
        if user:
            remaining = max(0, 5 - int(user.failed_logins or 0))
        tip = "邮箱或密码错误"
        if remaining and remaining <= 3:
            tip += f"，还可尝试 {remaining} 次"
        flash(tip, "error")

    return render_template("login.html", form=form)


@auth_bp.route("/logout", methods=["GET", "POST"])
@login_required
def logout():
    username = current_user.username
    logout_user()
    session.clear()
    flash(f"已安全退出，期待你再来，{username}！", "info")
    return redirect(url_for("main.index"))


def _login_now(user: User, remember: bool = False) -> None:
    user.last_login_at = utcnow()
    user.login_count = (user.login_count or 0) + 1
    db.session.commit()
    login_user(user, remember=remember, duration=timedelta(days=14))


# --------------------------------------------------------------------------
# 权限装饰器
# --------------------------------------------------------------------------
def admin_required(func):
    """要求当前用户已登录且为管理员。"""
    @wraps(func)
    @login_required
    def wrapper(*args, **kwargs):
        if not getattr(current_user, "is_admin", False):
            abort(403)
        return func(*args, **kwargs)
    return wrapper
