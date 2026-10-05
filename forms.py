"""WTForms 表单：注册 / 登录 / 修改资料 / 改密码。

所有表单默认启用 CSRF 校验（由 Flask-WTF CSRFProtect 统一提供）。
"""
from __future__ import annotations

import re

from flask_wtf import FlaskForm
from wtforms import BooleanField, PasswordField, StringField, SubmitField
from wtforms.validators import (DataRequired, Email, EqualTo, InputRequired, Length,
                                Optional, Regexp, ValidationError)

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
USERNAME_RE = re.compile(r"^[\w\u4e00-\u9fa5·.\-]{2,32}$")


def password_strength_check(form, field) -> None:
    """密码强度：长度 ≥ 8，且必须同时含字母和数字。"""
    from flask import current_app

    pwd = field.data or ""
    min_len = current_app.config.get("PASSWORD_MIN_LENGTH", 8)
    if len(pwd) < min_len:
        raise ValidationError(f"密码长度至少 {min_len} 位")
    if pwd.isdigit() or pwd.isalpha():
        raise ValidationError("密码需同时包含字母和数字")
    if pwd.strip().lower() == "password" or pwd.strip().lower() == "12345678":
        raise ValidationError("密码过于简单，请更换")


class RegistrationForm(FlaskForm):
    email = StringField(
        "邮箱",
        validators=[InputRequired("请输入邮箱"), Email(message="邮箱格式不正确"), Length(max=255)],
    )
    username = StringField(
        "用户名",
        validators=[
            InputRequired("请输入用户名"),
            Length(min=2, max=32, message="用户名长度需在 2-32 个字符之间"),
            Regexp(USERNAME_RE, message="用户名仅支持中英文、数字、下划线、点和短横线"),
        ],
    )
    password = PasswordField(
        "密码",
        validators=[InputRequired("请输入密码"), Length(max=128), password_strength_check],
    )
    confirm = PasswordField(
        "确认密码",
        validators=[InputRequired("请再次输入密码"), EqualTo("password", message="两次输入的密码不一致")],
    )
    agree = BooleanField("同意条款", validators=[])
    submit = SubmitField("注册")


class LoginForm(FlaskForm):
    email = StringField(
        "邮箱",
        validators=[InputRequired("请输入邮箱"), Email(message="邮箱格式不正确"), Length(max=255)],
    )
    password = PasswordField("密码", validators=[InputRequired("请输入密码")])
    remember = BooleanField("记住我")
    submit = SubmitField("登录")


class ProfileForm(FlaskForm):
    username = StringField(
        "用户名",
        validators=[
            InputRequired("请输入用户名"),
            Length(min=2, max=32, message="用户名长度需在 2-32 个字符之间"),
            Regexp(USERNAME_RE, message="用户名仅支持中英文、数字、下划线、点和短横线"),
        ],
    )
    submit = SubmitField("保存")


class ChangePasswordForm(FlaskForm):
    current_password = PasswordField("当前密码", validators=[DataRequired("请输入当前密码")])
    new_password = PasswordField(
        "新密码", validators=[InputRequired("请输入新密码"), Length(max=128), password_strength_check]
    )
    confirm = PasswordField(
        "确认新密码", validators=[InputRequired("请再次输入新密码"), EqualTo("new_password", message="两次输入的密码不一致")]
    )
    submit = SubmitField("修改密码")


class ForgotPasswordForm(FlaskForm):
    """忘记密码：提交注册邮箱以接收重置链接。"""

    email = StringField(
        "邮箱",
        validators=[InputRequired("请输入邮箱"), Email(message="邮箱格式不正确"), Length(max=255)],
    )
    submit = SubmitField("发送重置链接")


class ResetPasswordForm(FlaskForm):
    """通过重置链接设置新密码（链接内已含 token，表单只收密码）。"""

    password = PasswordField(
        "新密码", validators=[InputRequired("请输入新密码"), Length(max=128), password_strength_check]
    )
    confirm = PasswordField(
        "确认新密码",
        validators=[InputRequired("请再次输入新密码"), EqualTo("password", message="两次输入的密码不一致")],
    )
    submit = SubmitField("设置新密码")


class EmptyForm(FlaskForm):
    """仅用于 CSRF 保护（收藏 / 取消收藏等按钮）。"""

    submit = SubmitField("提交")


# --------------------------------------------------------------------------
# 邮箱验证码（无密码登录 / 注册）
# --------------------------------------------------------------------------
CODE_RE = re.compile(r"^\d{6}$")
# 验证码注册时用户名可选，允许空格（如 "Zhang San"），比密码注册宽松
USERNAME_LOOSE_RE = re.compile(r"^[\w\u4e00-\u9fa5·.\-\s]{2,32}$")


class CodeLoginForm(FlaskForm):
    """邮箱验证码登录：邮箱 + 6 位验证码，不需要密码。"""

    email = StringField(
        "邮箱",
        validators=[InputRequired("请输入邮箱"), Email(message="邮箱格式不正确"), Length(max=255)],
    )
    code = StringField(
        "验证码",
        validators=[
            InputRequired("请输入邮箱中的 6 位验证码"),
            Regexp(CODE_RE, message="验证码为 6 位数字"),
        ],
    )
    remember = BooleanField("记住我")
    submit = SubmitField("登录")


class CodeRegisterForm(FlaskForm):
    """邮箱验证码注册：邮箱 + 验证码即可创建账号，不设置密码。"""

    email = StringField(
        "邮箱",
        validators=[InputRequired("请输入邮箱"), Email(message="邮箱格式不正确"), Length(max=255)],
    )
    username = StringField(
        "用户名",
        validators=[
            Optional(),
            Length(min=2, max=32, message="用户名长度需在 2-32 个字符之间"),
            Regexp(USERNAME_LOOSE_RE, message="用户名仅支持中英文、数字、下划线、点和短横线"),
        ],
    )
    code = StringField(
        "验证码",
        validators=[
            InputRequired("请输入邮箱中的 6 位验证码"),
            Regexp(CODE_RE, message="验证码为 6 位数字"),
        ],
    )
    submit = SubmitField("注册")
