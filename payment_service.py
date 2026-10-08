"""商业化链路服务（规格 §17 / §19）：等级主数据 + 商品 / 订单 / 支付流水。

第一阶段是 **Mock Payment** —— 不接真实网关，但表结构、状态机与开通逻辑都按
真实链路设计；将来接支付宝 / 微信时，只需替换 :func:`pay_order` 里 provider
那一段，业务代码与数据表都不用改。

金额一律以**最小货币单位（分）**存整数，绝不用浮点，避免累计误差。
"""
from __future__ import annotations

import json
import logging
import secrets
from datetime import timedelta

from extensions import db
from models import Level, Order, Payment, Product, Subscription

logger = logging.getLogger(__name__)

#: 默认商品（幂等播种）
#: (code, name_zh, name_en, description, price_cents, currency, period, plan, sort)
DEFAULT_PRODUCTS = [
    ("monthly", "月度会员", "Premium Monthly",
     "解锁全部课程与复习计划 / Unlock every course and the review planner",
     2900, "CNY", "month", "premium", 0),
    ("yearly", "年度会员", "Premium Yearly",
     "全年畅学，折合每月不到 20 元 / Best value — under ¥20 a month",
     19900, "CNY", "year", "premium", 1),
    ("lifetime", "终身会员", "Lifetime Pro",
     "一次购买，永久有效 / Pay once, yours forever",
     49900, "CNY", "once", "pro", 2),
]

#: 订阅时长：period → 时长（None = 永久）
PERIOD_DELTA = {"month": timedelta(days=30), "year": timedelta(days=365), "once": None}


def ensure_levels() -> int:
    """把 CEFR 等级落进 levels 表（此前只存在于 learning_path 的常量里）。"""
    from learning_path import LEVELS
    if Level.query.count():
        return 0
    for lv in LEVELS:
        db.session.add(Level(code=lv.code, order=lv.order,
                             name_zh=lv.name_zh, name_en=lv.name_en,
                             color=lv.color, emoji=lv.emoji, units=3))
    db.session.commit()
    logger.info("levels 主数据已播种：%d 条", Level.query.count())
    return Level.query.count()


def ensure_products() -> int:
    """幂等播种默认商品（已存在则跳过，不覆盖运营改过的价格）。"""
    if Product.query.count():
        return 0
    for (code, zh, en, desc, cents, cur, period, plan, sort) in DEFAULT_PRODUCTS:
        db.session.add(Product(code=code, name_zh=zh, name_en=en, description=desc,
                               price_cents=cents, currency=cur, period=period,
                               active=True, sort=sort))
    db.session.commit()
    logger.info("products 已播种：%d 条", Product.query.count())
    return Product.query.count()


#: code → 对应订阅套餐（plan）
_PLAN_BY_CODE = {c: p for (c, _z, _e, _d, _m, _u, _per, p, _s) in DEFAULT_PRODUCTS}


def list_products(active_only: bool = True) -> list[Product]:
    q = Product.query
    if active_only:
        q = q.filter_by(active=True)
    return q.order_by(Product.sort.asc()).all()


def _new_order_no() -> str:
    return "OD" + secrets.token_hex(8).upper()


def create_order(user_id: int, product_code: str) -> Order:
    """创建订单（pending）。商品不存在时抛 ValueError。"""
    product = Product.query.filter_by(code=product_code, active=True).first()
    if not product:
        raise ValueError(f"unknown product: {product_code}")
    order = Order(
        user_id=user_id, product_id=product.id,
        order_no=_new_order_no(),
        plan=_PLAN_BY_CODE.get(product.code, "premium"),
        amount_cents=product.price_cents, currency=product.currency,
        status="pending",
    )
    db.session.add(order)
    db.session.commit()
    return order


def pay_order(order_no: str, provider: str = "mock") -> tuple[Order, Payment]:
    """Mock 支付：把订单置为 paid，写支付流水，并开通/续期订阅。

    真实网关接入点就在本函数 —— 只需把 provider 分支换成网关下单/回调校验，
    其余（状态机、订阅开通）保持不变。
    """
    order = Order.query.filter_by(order_no=order_no).first()
    if not order:
        raise ValueError(f"unknown order: {order_no}")
    if order.status == "paid":
        return order, Payment.query.filter_by(order_id=order.id).first()

    payment = Payment(
        order_id=order.id, provider=provider,
        transaction_id="MOCK" + secrets.token_hex(10).upper(),
        amount_cents=order.amount_cents, currency=order.currency,
        status="success",
        raw=json.dumps({"provider": provider, "mock": True,
                        "order_no": order.order_no}, ensure_ascii=False),
    )
    db.session.add(payment)

    from models import utcnow
    now = utcnow()
    order.status = "paid"
    order.paid_at = now

    # ── 开通 / 续期订阅 ──────────────────────────────────────────────
    product = Product.query.get(order.product_id) if order.product_id else None
    period = (product.period if product else "month") or "month"
    delta = PERIOD_DELTA.get(period)

    sub = Subscription.query.get(order.user_id)
    if not sub:
        sub = Subscription(user_id=order.user_id)
        db.session.add(sub)
    sub.plan = order.plan or "premium"
    sub.status = "active"
    sub.started_at = sub.started_at or now
    if delta is None:                     # once → 终身有效
        sub.expires_at = None
    else:
        # 续期：仍在有效期内则从到期日叠加，否则从现在起算
        base = sub.expires_at if (sub.expires_at and sub.expires_at > now) else now
        sub.expires_at = base + delta

    db.session.commit()
    logger.info("订单已支付：%s plan=%s", order.order_no, sub.plan)
    return order, payment


def user_orders(user_id: int, limit: int = 20) -> list[Order]:
    return (Order.query.filter_by(user_id=user_id)
            .order_by(Order.created_at.desc()).limit(limit).all())
