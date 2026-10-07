"""V5.7 / V5.8 能力权限（entitlements）判定（顶层模块，勿放 services/ 包）。

规格：AI Tutor 与 Speaking 属 **PRO**；常规学习（课程 / 练习 / 复习）三档通用。

设计要点
--------
* 单一真源：功能 → 可用套餐的映射集中在这张表里，路由层不再散落 if。
* 默认向下兼容：``Subscription`` 记录缺失时视为 ``free``（新建用户没有订阅行），
  **绝不能**因为缺记录就抛异常把功能搞挂。
* 过期订阅视为 free —— 只看 ``status`` 不看 ``expires_at`` 会让已过期用户
  继续白嫖 PRO。
* 支付链路当前是 mock（见 ``payment_service``），套餐由后台直接改写，
  因此这里的判定必须是「读一次即得」，不依赖任何外部服务。
"""
from __future__ import annotations

from datetime import datetime

from extensions import db
from models import Subscription, utcnow

#: 套餐层级（数值越大权限越高，便于比较）
PLAN_RANK = {"free": 0, "premium": 1, "pro": 2}

#: 功能 → 允许使用的套餐集合
FEATURE_PLANS: dict[str, tuple[str, ...]] = {
    # 常规学习：三档都能用
    "learning": ("free", "premium", "pro"),
    "practice": ("free", "premium", "pro"),
    "review": ("free", "premium", "pro"),
    # AI 能力：仅 PRO
    "ai_tutor": ("pro",),
    # 口语 / 听力评分：仅 PRO
    "speaking": ("pro",),
    "listening": ("free", "premium", "pro"),
}

UPGRADE_HINT = "该功能为 PRO 专享，升级后立即可用"


def plan_of(user_id: int) -> str:
    """取用户当前有效套餐；无记录 / 已过期 / 非 active 一律按 free 处理。"""
    try:
        sub = db.session.get(Subscription, int(user_id))
    except Exception:  # pragma: no cover - 缺表等异常不能让功能挂掉
        return "free"
    if sub is None:
        return "free"
    plan = (sub.plan or "free").strip().lower()
    if plan not in PLAN_RANK:
        plan = "free"
    if (sub.status or "active").strip().lower() != "active":
        return "free"
    expires = sub.expires_at
    if expires is not None and isinstance(expires, datetime) and expires < utcnow():
        return "free"
    return plan


def allowed_plans(feature: str) -> tuple[str, ...]:
    return FEATURE_PLANS.get((feature or "").strip().lower(), ("free", "premium", "pro"))


def has(feature: str, user_id: int) -> bool:
    return plan_of(user_id) in allowed_plans(feature)


def check(feature: str, user_id: int) -> dict:
    """统一返回判定结果，供路由层与模板共用。"""
    plan = plan_of(user_id)
    ok = plan in allowed_plans(feature)
    return {
        "allowed": ok,
        "feature": feature,
        "plan": plan,
        "required": list(allowed_plans(feature)),
        "hint": None if ok else UPGRADE_HINT,
    }