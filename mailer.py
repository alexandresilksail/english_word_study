"""极简邮件发送（仅用于密码重置 / 邮箱验证）。

设计取向
--------
- **不强依赖 SMTP**：未配置 ``MAIL_SERVER`` 时返回 False，调用方降级为
  “在页面上给出链接 / 写入日志”，这样本地开发与未配邮件的部署也能跑通流程，
  不会因为缺少 SMTP 而导致注册或找回密码 500。
- 仅使用标准库 ``smtplib``，不引入新依赖。
"""
from __future__ import annotations

import logging
import os
import smtplib
from email.message import EmailMessage

logger = logging.getLogger(__name__)


def _cfg(key: str, default: str = "") -> str:
    return (os.environ.get(key) or default).strip()


def mail_enabled() -> bool:
    return bool(_cfg("MAIL_SERVER"))


def send_email(to_addr: str, subject: str, body: str) -> bool:
    """发送纯文本邮件。成功返回 True；未配置 SMTP 或失败返回 False。"""
    server = _cfg("MAIL_SERVER")
    if not server:
        logger.info("[mailer] 未配置 MAIL_SERVER，跳过发送。收件人=%s 主题=%s", to_addr, subject)
        return False

    port = int(_cfg("MAIL_PORT", "587") or 587)
    username = _cfg("MAIL_USERNAME")
    password = _cfg("MAIL_PASSWORD")
    use_tls = _cfg("MAIL_USE_TLS", "true").lower() in ("1", "true", "yes")
    sender = _cfg("MAIL_FROM") or username or "noreply@example.com"

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = sender
    msg["To"] = to_addr
    msg.set_content(body)

    try:
        with smtplib.SMTP(server, port, timeout=15) as smtp:
            if use_tls:
                smtp.starttls()
            if username and password:
                smtp.login(username, password)
            smtp.send_message(msg)
        logger.info("[mailer] 已发送邮件至 %s", to_addr)
        return True
    except Exception as exc:
        logger.warning("[mailer] 发送失败：%s", exc)
        return False
