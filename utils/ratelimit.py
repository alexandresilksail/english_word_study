"""登录失败限制。

既做 IP 维度的轻量计数（内存），也做账户维度的锁定（落库），
避免单独 IP 限流被绕过的同时防止同一账户被反复爆破。
"""
from __future__ import annotations

import threading
import time
from collections import defaultdict, deque

from flask import request

_LOCK = threading.Lock()
_IP_HITS: dict[str, deque] = defaultdict(deque)
_IP_LIMIT = 20          # 单个 IP 在窗口期内最多尝试次数
_IP_WINDOW = 600.0      # 窗口期：10 分钟


def ip_too_many_requests() -> bool:
    ip = (request.headers.get("X-Forwarded-For", "").split(",")[0].strip()
          or request.headers.get("X-Real-IP", "")
          or (request.remote_addr or "unknown"))
    now = time.time()
    with _LOCK:
        hits = _IP_HITS[ip]
        while hits and now - hits[0] > _IP_WINDOW:
            hits.popleft()
        if len(hits) >= _IP_LIMIT:
            return True
        hits.append(now)
    return False


def register_login_failure(user) -> tuple[int, int]:
    """记录一次失败登录，返回 (剩余可尝试次数, 还需等待秒数)。"""
    from models import utcnow
    from datetime import timedelta

    if getattr(user, "is_locked", False):
        left = int((user.locked_until - utcnow()).total_seconds())
        return 0, max(left, 0)

    user.failed_logins = int(user.failed_logins or 0) + 1
    limit = 5
    wait = 0
    remaining = max(limit - user.failed_logins, 0)
    if user.failed_logins >= limit:
        user.locked_until = utcnow() + timedelta(minutes=15)
        wait = 15 * 60
    from extensions import db
    db.session.commit()
    return remaining, wait


def clear_login_failures(user) -> None:
    if user.failed_logins or user.locked_until:
        user.failed_logins = 0
        user.locked_until = None
        from extensions import db
        db.session.commit()
