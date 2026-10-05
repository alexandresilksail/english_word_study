"""用户认证：注册 / 登录 / 退出 + 权限装饰器。

安全要点：
- 密码使用 Werkzeug pbkdf2:sha256 哈希，绝不明文保存
- 登录失败达到阈值后临时锁定账户
- 退出登录会清空 session 并记住我 cookie
"""
from __future__ import annotations

import secrets
from datetime import timedelta
from functools import wraps

from flask import (Blueprint, abort, flash, jsonify, redirect, render_template,
                   request, session, url_for)
from flask_login import current_user, login_required, login_user, logout_user

from email_code import issue_code, verify_code
from extensions import db
from forms import (CodeLoginForm, CodeRegisterForm, ForgotPasswordForm,
                   LoginForm, RegistrationForm, ResetPasswordForm)
from mailer import send_email
from models import User, utcnow
from utils.ratelimit import clear_login_failures, register_login_failure

# 重置链接有效期
RESET_TOKEN_TTL_MINUTES = 60

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

    # 默认走「邮箱验证码注册（无密码）」，仍保留 ?mode=password 的密码注册
    mode = (request.args.get("mode") or "code").strip().lower()
    if mode not in ("code", "password"):
        mode = "code"

    form = RegistrationForm()
    if form.validate_on_submit():
        email = (form.email.data or "").strip().lower()
        if User.query.filter_by(email=email).first():
            flash("该邮箱已被注册，请直接登录", "error")
            return render_template("register.html", form=form, code_form=CodeRegisterForm(), mode="password")

        user = User(email=email, username=(form.username.data or "").strip())
        user.set_password(form.password.data)
        user.verify_token = secrets.token_urlsafe(32)
        db.session.add(user)
        db.session.commit()

        _ = session.get("_flashes")  # touch session ensure cookie exists
        flash("注册成功！已自动登录，开始背单词吧", "success")

        # 发送验证邮件；未配置 SMTP 时把链接直接给到页面（便于本地验证流程）
        verify_url = url_for("auth.verify_email", token=user.verify_token, _external=True)
        if send_email(
            user.email,
            "请验证你的邮箱 · English Word Study",
            f"你好 {user.username}，\n\n请点击下面的链接完成邮箱验证：\n{verify_url}\n\n"
            "如果不是你本人操作，请忽略本邮件。",
        ):
            flash("验证邮件已发送，请查收邮箱。", "info")
        else:
            flash(f"（邮件未配置 SMTP）邮箱验证链接：{verify_url}", "info")

        _login_now(user, remember=True)
        return redirect(url_for("main.dashboard"))

    return render_template("register.html", form=form, code_form=CodeRegisterForm(), mode=mode)


@auth_bp.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for("main.dashboard"))

    form = LoginForm()
    # 默认展示「邮箱验证码登录」，仍保留 ?tab=password 的密码登录
    tab = (request.args.get("tab") or "code").strip().lower()
    if tab not in ("code", "password"):
        tab = "code"
    code_form = CodeLoginForm()
    if form.validate_on_submit():
        email = (form.email.data or "").strip().lower()
        user = User.query.filter_by(email=email).first()
        ok = False

        if user:
            remaining, wait_seconds = register_login_failure(user)
            if user.is_locked:
                flash(f"登录失败次数过多，请 {wait_seconds // 60 + 1} 分钟后再试", "error")
                return render_template("login.html", form=form, code_form=code_form, tab="password")
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
        return render_template("login.html", form=form, code_form=code_form, tab="password")

    return render_template("login.html", form=form, code_form=code_form, tab=tab)


# --------------------------------------------------------------------------
# 邮箱验证码（无密码）注册 / 登录
# --------------------------------------------------------------------------
@auth_bp.route("/auth/code/request", methods=["POST"])
def request_code():
    """申请邮箱验证码（AJAX）。返回 JSON：{ok, message, dev_code}。

    ``dev_code`` 仅在未配置 SMTP 时出现，便于本地自测。
    """
    if current_user.is_authenticated:
        return jsonify({"ok": False, "message": "已登录 / Already signed in"}), 400

    data = request.get_json(silent=True) or request.form
    email = ((data.get("email") if hasattr(data, "get") else "") or "").strip().lower()
    purpose = ((data.get("purpose") if hasattr(data, "get") else "") or "login").strip()
    if purpose not in ("login", "register"):
        purpose = "login"

    if "@" not in email or email.startswith("@") or email.endswith("@"):
        return jsonify({"ok": False, "message": "请输入有效的邮箱地址 / Enter a valid email"}), 400

    exists = User.query.filter_by(email=email).first() is not None
    if purpose == "login" and not exists:
        return jsonify({"ok": False, "message": "该邮箱尚未注册，请先注册 / Not registered yet"}), 400
    if purpose == "register" and exists:
        return jsonify({"ok": False, "message": "该邮箱已注册，请直接登录 / Already registered, sign in"}), 400

    ok, message, dev_code = issue_code(email, purpose, request.remote_addr)
    return jsonify({"ok": ok, "message": message, "dev_code": dev_code}), (200 if ok else 429)


@auth_bp.route("/auth/code/login", methods=["POST"])
def code_login():
    """邮箱验证码登录：校验通过即登录，无需密码。"""
    if current_user.is_authenticated:
        return redirect(url_for("main.dashboard"))

    code_form = CodeLoginForm()
    if code_form.validate_on_submit():
        email = (code_form.email.data or "").strip().lower()
        user = User.query.filter_by(email=email).first()
        if not user:
            flash("该邮箱尚未注册，请先创建账号 / Not registered yet", "error")
            return render_template("login.html", form=LoginForm(), code_form=code_form, tab="code")

        ok, message = verify_code(email, code_form.code.data, "login")
        if not ok:
            flash(message, "error")
            return render_template("login.html", form=LoginForm(), code_form=code_form, tab="code")

        # 能用邮箱里的验证码完成校验，即视为邮箱归属已确认
        if not user.email_verified:
            user.email_verified = True
        clear_login_failures(user)
        db.session.commit()

        _login_now(user, remember=bool(code_form.remember.data))
        flash(f"欢迎回来，{user.username}！", "success")
        return redirect(_safe_next(request.args.get("next")) or url_for("main.dashboard"))

    return render_template("login.html", form=LoginForm(), code_form=code_form, tab="code")


@auth_bp.route("/auth/code/register", methods=["POST"])
def code_register():
    """邮箱验证码注册：不设置密码，校验通过即创建账号并登录。"""
    if current_user.is_authenticated:
        return redirect(url_for("main.dashboard"))

    code_form = CodeRegisterForm()
    if code_form.validate_on_submit():
        email = (code_form.email.data or "").strip().lower()
        if User.query.filter_by(email=email).first():
            flash("该邮箱已被注册，请直接登录 / Already registered, sign in", "error")
            return render_template("register.html", form=RegistrationForm(), code_form=code_form, mode="code")

        ok, message = verify_code(email, code_form.code.data, "register")
        if not ok:
            flash(message, "error")
            return render_template("register.html", form=RegistrationForm(), code_form=code_form, mode="code")

        username = (code_form.username.data or "").strip() or email.split("@")[0][:32]
        if len(username) < 2:
            username = (email.split("@")[0] or "Learner")[:32]

        user = User(email=email, username=username)
        user.set_unusable_password()      # 无密码账号：密码哈希为随机值，用户并不知晓
        user.email_verified = True        # 已完成邮箱验证码校验
        db.session.add(user)
        db.session.commit()

        flash("注册成功！已自动登录，开始背单词吧 / Account created, you're signed in", "success")
        _login_now(user, remember=True)
        return redirect(url_for("main.dashboard"))

    return render_template("register.html", form=RegistrationForm(), code_form=code_form, mode="code")


@auth_bp.route("/logout", methods=["GET", "POST"])
@login_required
def logout():
    username = current_user.username
    logout_user()
    session.clear()
    flash(f"已安全退出，期待你再来，{username}！", "info")
    return redirect(url_for("main.index"))


@auth_bp.route("/forgot-password", methods=["GET", "POST"])
def forgot_password():
    """忘记密码：提交邮箱后发送重置链接。

    安全：无论邮箱是否注册，都返回同样的提示，避免账号枚举。
    """
    if current_user.is_authenticated:
        return redirect(url_for("main.dashboard"))

    form = ForgotPasswordForm()
    if form.validate_on_submit():
        email = (form.email.data or "").strip().lower()
        user = User.query.filter_by(email=email).first()
        if user:
            token = secrets.token_urlsafe(32)
            user.reset_token = token
            user.reset_token_exp = utcnow() + timedelta(minutes=RESET_TOKEN_TTL_MINUTES)
            db.session.commit()

            reset_url = url_for("auth.reset_password", token=token, _external=True)
            if not send_email(
                user.email,
                "重置你的密码 · English Word Study",
                f"你好 {user.username}，\n\n请点击下面的链接重置密码（{RESET_TOKEN_TTL_MINUTES} 分钟内有效）：\n"
                f"{reset_url}\n\n如果不是你本人操作，请忽略本邮件，你的密码不会改变。",
            ):
                # 未配置 SMTP：直接在页面给出链接，保证流程可走通
                flash(f"（邮件未配置 SMTP）密码重置链接：{reset_url}", "info")
        flash("如果该邮箱已注册，重置链接已发送，请注意查收。", "info")
        return redirect(url_for("auth.login"))

    return render_template("forgot_password.html", form=form)


@auth_bp.route("/reset-password/<token>", methods=["GET", "POST"])
def reset_password(token: str):
    """通过邮件中的 token 设置新密码。"""
    if current_user.is_authenticated:
        return redirect(url_for("main.dashboard"))

    user = User.query.filter_by(reset_token=token).first()
    if not user or not user.reset_token_exp or user.reset_token_exp < utcnow():
        flash("重置链接无效或已过期，请重新申请。", "error")
        return redirect(url_for("auth.forgot_password"))

    form = ResetPasswordForm()
    if form.validate_on_submit():
        user.set_password(form.password.data)
        user.reset_token = None
        user.reset_token_exp = None
        db.session.commit()
        flash("密码已重置，请使用新密码登录。", "success")
        return redirect(url_for("auth.login"))

    return render_template("reset_password.html", form=form)


@auth_bp.route("/verify-email/<token>")
def verify_email(token: str):
    """邮箱验证。注意：验证与否不影响登录，仅作标记，避免影响既有老用户。"""
    user = User.query.filter_by(verify_token=token).first()
    if not user:
        flash("验证链接无效。", "error")
        return redirect(url_for("main.index"))

    user.email_verified = True
    user.verify_token = None
    db.session.commit()
    flash("邮箱验证成功，谢谢！", "success")
    return redirect(url_for("main.dashboard") if current_user.is_authenticated else url_for("auth.login"))


@auth_bp.route("/resend-verification", methods=["POST"])
@login_required
def resend_verification():
    """重新发送验证邮件。"""
    user = current_user
    if user.email_verified:
        flash("你的邮箱已经验证过了。", "info")
        return redirect(url_for("main.profile"))

    if not user.verify_token:
        user.verify_token = secrets.token_urlsafe(32)
        db.session.commit()

    verify_url = url_for("auth.verify_email", token=user.verify_token, _external=True)
    if send_email(
        user.email,
        "请验证你的邮箱 · English Word Study",
        f"你好 {user.username}，\n\n请点击下面的链接完成邮箱验证：\n{verify_url}\n\n"
        "如果不是你本人操作，请忽略本邮件。",
    ):
        flash("验证邮件已重新发送，请查收。", "info")
    else:
        flash(f"（邮件未配置 SMTP）邮箱验证链接：{verify_url}", "info")
    return redirect(url_for("main.profile"))


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
