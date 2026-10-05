"""邮箱验证码（Email OTP）：生成 / 发送 / 校验 / 限流。

设计
----
- **一次性**：校验成功立即删除记录，不可复用
- **明文不落库**：库中只保存 pbkdf2 哈希，明文只出现在邮件里
- **有效期**：10 分钟；错误 5 次即失效
- **限流**：60 秒内不可重发；同一邮箱 1 小时内最多 10 次
- **降级**：未配置 SMTP 时返回明文码（dev_code）交给页面展示，
  保证本地开发与自测能走通，不会因缺邮件服务而卡死注册/登录
"""
from __future__ import annotations

import logging
import secrets
from datetime import timedelta

from werkzeug.security import check_password_hash, generate_password_hash

from extensions import db
from mailer import send_email
from models import EmailCode, utcnow

logger = logging.getLogger(__name__)

CODE_TTL_MINUTES = 10          # 验证码有效时长
RESEND_INTERVAL_SEC = 60       # 两次发送的最小间隔
MAX_ATTEMPTS = 5               # 单个验证码最多可错次数
MAX_PER_HOUR = 10              # 同一邮箱 1 小时内最多发送次数
CODE_LENGTH = 6                # 6 位数字


def generate_code() -> str:
    """生成 6 位数字验证码（密码学安全随机）。"""
    return "".join(str(secrets.randbelow(10)) for _ in range(CODE_LENGTH))


def _mail_subject(purpose: str) -> str:
    return "你的登录验证码 · English Word Study"


def _mail_body(code: str, purpose: str) -> str:
    action = "登录" if purpose == "login" else "注册"
    return (
        f"你的验证码是：{code}\n\n"
        f"用途：{action} English Word Study（英语单词学习）\n"
        f"有效期：{CODE_TTL_MINUTES} 分钟，过期请重新获取。\n\n"
        "如果这不是你本人的操作，请忽略本邮件，你的账号仍然是安全的。\n\n"
        "——————————————\n"
        f"Your verification code is: {code}\n"
        f"Use it to {'sign in to' if purpose == 'login' else 'create'} your English Word Study account.\n"
        f"It expires in {CODE_TTL_MINUTES} minutes.\n"
        "If you did not request this, simply ignore this email.\n"
    )


def issue_code(email: str, purpose: str, ip: str | None = None) -> tuple[bool, str, str | None]:
    """生成并发送验证码。

    返回 ``(ok, message, dev_code)``：
    - ``ok`` 为 True 表示已发出（或已降级给出 dev_code）
    - ``dev_code`` 仅在未配置 SMTP 时非空，供页面提示使用
    """
    email = (email or "").strip().lower()
    purpose = purpose if purpose in ("login", "register") else "login"
    now = utcnow()

    # ---- 限流 1：60 秒内不可重发 ----
    recent = (
        EmailCode.query.filter_by(email=email, purpose=purpose)
        .order_by(EmailCode.created_at.desc())
        .first()
    )
    if recent and (now - recent.created_at).total_seconds() < RESEND_INTERVAL_SEC:
        wait = int(RESEND_INTERVAL_SEC - (now - recent.created_at).total_seconds())
        return False, f"验证码已发送，请 {wait} 秒后再试 / Please wait {wait}s before resending", None

    # ---- 限流 2：1 小时内最多 MAX_PER_HOUR 次 ----
    hourly = EmailCode.query.filter(
        EmailCode.email == email,
        EmailCode.created_at >= now - timedelta(hours=1),
    ).count()
    if hourly >= MAX_PER_HOUR:
        return False, "请求过于频繁，请稍后再试 / Too many requests, try later", None

    # ---- 作废该邮箱该用途的旧码，保证同时只有一个有效 ----
    EmailCode.query.filter_by(email=email, purpose=purpose).delete()
    db.session.flush()

    code = generate_code()
    row = EmailCode(
        email=email,
        code_hash=generate_password_hash(code, method="pbkdf2:sha256", salt_length=16),
        purpose=purpose,
        created_at=now,
        expires_at=now + timedelta(minutes=CODE_TTL_MINUTES),
        attempts=0,
        ip=(ip or "")[:64] or None,
    )
    db.session.add(row)
    db.session.commit()

    sent = send_email(email, _mail_subject(purpose), _mail_body(code, purpose))
    if sent:
        logger.info("[email_code] 已向 %s 发送 %s 验证码", email, purpose)
        return True, "验证码已发送，请查收邮箱 / Code sent, please check your inbox", None

    # 未配置 SMTP：降级，把明文码交给页面（仅本地/自测使用）
    logger.warning("[email_code] 未配置 SMTP，验证码降级为页面展示：%s -> %s", email, code)
    return True, "（未配置邮件服务）验证码已在下方显示 / Email not configured, code shown below", code


def verify_code(email: str, code: str, purpose: str) -> tuple[bool, str]:
    """校验验证码。成功返回 ``(True, "")`` 并立即作废该码。"""
    email = (email or "").strip().lower()
    code = (code or "").strip()
    if not email or not code:
        return False, "请输入邮箱和验证码 / Enter your email and code"

    row = (
        EmailCode.query.filter_by(email=email, purpose=purpose)
        .order_by(EmailCode.created_at.desc())
        .first()
    )
    if not row or row.consumed_at:
        return False, "验证码无效或已使用，请重新获取 / Invalid or used code, request a new one"
    if row.is_expired:
        db.session.delete(row)
        db.session.commit()
        return False, "验证码已过期，请重新获取 / Code expired, request a new one"
    if int(row.attempts or 0) >= MAX_ATTEMPTS:
        db.session.delete(row)
        db.session.commit()
        return False, "错误次数过多，请重新获取验证码 / Too many attempts, request a new one"

    if not check_password_hash(row.code_hash, code):
        row.attempts = int(row.attempts or 0) + 1
        db.session.commit()
        left = max(0, MAX_ATTEMPTS - int(row.attempts or 0))
        return False, f"验证码不正确，还可尝试 {left} 次 / Wrong code, {left} attempts left"

    # 一次性：校验通过立即删除
    db.session.delete(row)
    db.session.commit()
    return True, ""


def purge_expired() -> int:
    """清理已过期/已使用的验证码，返回删除条数（可在维护任务中调用）。"""
    n = EmailCode.query.filter(
        (EmailCode.expires_at < utcnow()) | (EmailCode.consumed_at.isnot(None))
    ).delete()
    if n:
        db.session.commit()
    return n
