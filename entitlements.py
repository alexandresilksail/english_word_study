"""V5.7 / V5.8 能力权限（entitlements）判定 + 每日配额（顶层模块，勿放 services/ 包）。

规格：AI Tutor 与 Speaking 的**完整使用权属 PRO**；常规学习（课程 / 练习 / 复习）
三档通用。

两道闸门
--------
1. **套餐门**（``FEATURE_PLANS``）—— 决定「无限用」。命中即不限量。
2. **配额门**（``DAILY_TRIAL_QUOTAS``）—— 决定「每天能试用几次」。
   低档套餐没被套餐门放行时，走这里给一个**有限的每日试用额度**。

   分成两张表而不是一张，是为了让原规格「AI 与 Speaking = PRO」保持字面成立：
   ``allowed_plans("ai_tutor")`` 仍然只返回 ``("pro",)``，低档用户拿到的
   是「临时试用」而不是「获得授权」。UI 据此显示「今日试用还剩 N 次」，
   而不是「已解锁」。

诚实原则
--------
* 默认向下兼容：``Subscription`` 记录缺失时视为 ``free``（新建用户没有订阅行），
  **绝不能**因为缺记录就抛异常把功能搞挂。
* 过期订阅视为 free —— 只看 ``status`` 不看 ``expires_at`` 会让已过期用户
  继续白嫖 PRO。
* 配额读取失败时**放行**而不是拒绝：计数表出问题不该让已付费用户用不了
  已付费功能（宁可少收钱，不能砸服务）。反之，计数自增失败不影响主流程。
* 支付链路当前是 mock（见 ``payment_service``），套餐由后台直接改写，
  因此这里的判定必须是「读一次即得」，不依赖任何外部服务。

日期口径
--------
配额按 **UTC 自然日** 重置。用服务器本地时间会让「今天」的长度随部署地漂移，
用户会遇到「明明说是每天重置，怎么刚到下午就没了」。

.. warning:: 并发

   计数自增是「读-改-写」三步，多进程并发下可能少计（不会多计）。
   本项目是 2vCPU / Gunicorn 单 worker 的部署规模，精度足够；
   将来要多 worker 横向扩展，改成 ``UPDATE ... SET count = count + 1``
   的原子语句即可，接口不变。
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timezone

from extensions import db
from models import Subscription, UsageCounter, utcnow

logger = logging.getLogger(__name__)

#: 套餐层级（数值越大权限越高，便于比较）
PLAN_RANK = {"free": 0, "premium": 1, "pro": 2}

#: 功能 → 拥有**无限使用权**的套餐集合（原规格：AI 与 Speaking = PRO）
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

#: 功能 → 套餐 → 每日试用次数；``None`` 表示不限量（由 FEATURE_PLANS 放行）
DAILY_TRIAL_QUOTAS: dict[str, dict[str, int | None]] = {
    "ai_tutor": {"free": 3, "premium": 20, "pro": None},
    "speaking": {"free": 5, "premium": 30, "pro": None},
}


def _apply_env_overrides() -> None:
    """允许用环境变量调整额度：``TRIAL_QUOTA_<FEATURE>_<PLAN>``。

    例：``TRIAL_QUOTA_AI_TUTOR_FREE=10`` 把免费档 AI 试用从 3 次提到 10 次。

    为什么需要它：额度是运营参数，活动期间要调、灰度时要收紧。
    写死在代码里意味着每次调整都要改代码 + 走发布流程；
    放进 .env 则改完重启即可，也方便不同环境（预发 / 生产）给不同值。

    非法值（非数字 / 负数）**忽略并保留默认值**，绝不因为一个笔误把
    功能直接关掉（``0`` 是合法值，表示「不给试用」）。
    """
    for feature, table in DAILY_TRIAL_QUOTAS.items():
        for plan in list(table):
            raw = os.environ.get(f"TRIAL_QUOTA_{feature.upper()}_{plan.upper()}")
            if raw is None or not str(raw).strip():
                continue
            try:
                table[plan] = max(0, int(str(raw).strip()))
            except (TypeError, ValueError):
                logger.warning("忽略非法的额度环境变量 %s=%r",
                               f"TRIAL_QUOTA_{feature.upper()}_{plan.upper()}", raw)


_apply_env_overrides()

#: 计数表保留天数（更老的行已无查询价值，定期清掉避免无限膨胀）
USAGE_RETENTION_DAYS = 90

UPGRADE_HINT = "该功能为 PRO 专享，升级后立即可用"
QUOTA_HINT = "今日试用次数已用完，升级 PRO 即可不限量使用"


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
    """拥有**无限使用权**的套餐（不含每日试用额度）。"""
    return FEATURE_PLANS.get((feature or "").strip().lower(), ("free", "premium", "pro"))


def has(feature: str, user_id: int) -> bool:
    return check(feature, user_id)["allowed"]


# --------------------------------------------------------------------------
# 每日配额
# --------------------------------------------------------------------------
def today() -> str:
    """UTC 自然日 ``YYYY-MM-DD``。配额重置的口径，全站唯一。"""
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def daily_limit(feature: str, plan: str) -> int | None:
    """该套餐在该功能上的每日试用上限；``None`` = 不限量。"""
    table = DAILY_TRIAL_QUOTAS.get((feature or "").strip().lower())
    if not table:
        return None  # 未纳入配额管理的功能：不限量
    return table.get((plan or "free").strip().lower())


def used_today(user_id: int, feature: str) -> int:
    """今日已用次数。读取异常时返回 0（宁可放行，不能砸服务）。"""
    try:
        row = (UsageCounter.query
               .filter_by(user_id=int(user_id), feature=feature, day=today())
               .first())
        return int(row.count) if row else 0
    except Exception as exc:  # pragma: no cover - 计数表异常不能阻断功能
        logger.warning("用量读取失败，按 0 处理（%s）", exc)
        db.session.rollback()
        return 0


def consume(user_id: int, feature: str, amount: int = 1) -> int:
    """实际执行成功后自增计数，返回自增后的今日累计值。

    只在**成功路径**调用：参数错误（422）之类的失败请求不该占用额度。
    """
    feature = (feature or "").strip().lower()
    try:
        row = (UsageCounter.query
               .filter_by(user_id=int(user_id), feature=feature, day=today())
               .first())
        if row is None:
            row = UsageCounter(user_id=int(user_id), feature=feature, day=today(), count=0)
            db.session.add(row)
            db.session.flush()
        row.count = int(row.count or 0) + max(1, int(amount))
        row.updated_at = utcnow()
        db.session.commit()
        return int(row.count)
    except Exception as exc:  # pragma: no cover - 计数失败不影响主流程
        logger.warning("用量自增失败，忽略（%s）", exc)
        try:
            db.session.rollback()
        except Exception:  # pragma: no cover
            pass
        return used_today(user_id, feature)


def quota_state(feature: str, user_id: int, plan: str | None = None) -> dict:
    """配额快照，供 API 返回与前端「今日还剩 N 次」展示。"""
    feature = (feature or "").strip().lower()
    plan = plan or plan_of(user_id)
    limit = daily_limit(feature, plan)
    used = used_today(user_id, feature)
    unlimited = limit is None
    return {
        "limit": limit,               # None = 不限量
        "used": used,
        "remaining": None if unlimited else max(0, int(limit) - used),
        "unlimited": unlimited,
        "resets_at": "UTC 00:00",
        "day": today(),
    }


def purge_old_usage(days: int = USAGE_RETENTION_DAYS) -> int:
    """清理 N 天前的计��行（可挂定时任务），返回删除行数。"""
    cutoff = datetime.now(timezone.utc).date().toordinal() - max(1, int(days))
    cutoff_s = datetime.fromordinal(cutoff).strftime("%Y-%m-%d")
    try:
        n = UsageCounter.query.filter(UsageCounter.day < cutoff_s).delete()
        db.session.commit()
        return int(n or 0)
    except Exception as exc:  # pragma: no cover
        db.session.rollback()
        logger.warning("用量清理失败（%s）", exc)
        return 0


# --------------------------------------------------------------------------
# 统一判定
# --------------------------------------------------------------------------
def check(feature: str, user_id: int) -> dict:
    """套餐门 + 配额门的合并判定，供路由层与模板共用。

    返回结构::

        {
          "allowed": bool,
          "feature": str,
          "plan": str,
          "required": [...],           # 拥有无限使用权的套餐
          "by_plan": bool,             # 由套餐门放行（不限量）
          "by_quota": bool,            # 由每日试用额度放行（有限次）
          "hint": str | None,          # 被拒时的用户可见原因
          "code": str | None,          # 被拒时的错误码：plan_required / quota_exceeded
          "quota": {...},              # 配额快照（含 limit / used / remaining）
        }
    """
    feature = (feature or "").strip().lower()
    plan = plan_of(user_id)
    plans = allowed_plans(feature)

    if plan in plans:
        return {
            "allowed": True, "feature": feature, "plan": plan,
            "required": list(plans),
            "by_plan": True, "by_quota": False,
            "hint": None, "code": None,
            "quota": quota_state(feature, user_id, plan),
        }

    # 没被套餐门放行 —— 查每日试用额度
    limit = daily_limit(feature, plan)
    quota = quota_state(feature, user_id, plan)
    if limit is None:
        # 未纳入配额管理且不在允许套餐内：按套餐不足处理（保持原行为）
        return {
            "allowed": False, "feature": feature, "plan": plan,
            "required": list(plans),
            "by_plan": False, "by_quota": False,
            "hint": UPGRADE_HINT, "code": "plan_required",
            "quota": quota,
        }

    remaining = int(limit) - int(quota["used"])
    if remaining > 0:
        return {
            "allowed": True, "feature": feature, "plan": plan,
            "required": list(plans),
            "by_plan": False, "by_quota": True,
            "hint": None, "code": None,
            "quota": quota,
        }

    return {
        "allowed": False, "feature": feature, "plan": plan,
        "required": list(plans),
        "by_plan": False, "by_quota": False,
        "hint": QUOTA_HINT, "code": "quota_exceeded",
        "quota": quota,
    }
