"""轻量、幂等的增量列迁移。

背景
----
本轮为邮箱验证 / 密码重置在 users 表新增了 4 个字段（email_verified / verify_token /
reset_token / reset_token_exp）。但 ``db.create_all()`` 不会给**已存在**的表补列，
直接访问 ``user.email_verified`` 会报错，也会让老用户无法登录。

方案
----
启动时检查 users 表实际列，缺失的列用 ``ALTER TABLE ADD COLUMN`` 补上。
纯增量、不改既有列、不动任何数据 —— 老用户记录完整保留。

幂等：每次启动都安全执行，已存在则跳过。
"""
from __future__ import annotations

import logging

from sqlalchemy import inspect, text

from extensions import db

logger = logging.getLogger(__name__)

# 列名 -> (是否布尔, 长度/类型标识)
_EXTRA_COLUMNS = {
    "email_verified": "bool",
    "verify_token": "str64",
    "reset_token": "str64",
    "reset_token_exp": "datetime",
    # 无密码账号标记（邮箱验证码注册）
    "is_passwordless": "bool",
    # 界面语言偏好（zh/en/yue/both，可空=双语自动）
    "preferred_lang": "str8",
}

# 表名 -> {列名: 类型}（V5.1：内容的多语释义，保证 English 版面零中文）
_EXTRA_COLUMNS_BY_TABLE = {
    "content_items": {
        "meaning_en": "str512",
        "meaning_yue": "str512",
        "example_yue": "text",
    },
}


def _ddl_for(col_type: str, dialect: str) -> str:
    pg = dialect == "postgresql"
    if col_type == "bool":
        return "BOOLEAN DEFAULT FALSE NOT NULL" if pg else "BOOLEAN DEFAULT 0 NOT NULL"
    if col_type == "str64":
        return "VARCHAR(64)"
    if col_type == "str8":
        return "VARCHAR(8)"
    if col_type == "str255":
        return "VARCHAR(255)"
    if col_type == "str512":
        return "VARCHAR(512)"
    if col_type == "text":
        return "TEXT"
    if col_type == "str16":
        return "VARCHAR(16)"
    if col_type == "int":
        return "INTEGER DEFAULT 0 NOT NULL"
    if col_type == "datetime":
        return "TIMESTAMP" if pg else "DATETIME"
    return "VARCHAR(255)"


def ensure_user_columns(app) -> int:
    """补齐 users 表的新增列，返回实际补上的列数。"""
    added = 0
    with app.app_context():
        try:
            insp = inspect(db.engine)
            if "users" not in insp.get_table_names():
                return 0
            existing = {c["name"] for c in insp.get_columns("users")}
            dialect = db.engine.dialect.name
            for col, kind in _EXTRA_COLUMNS.items():
                if col in existing:
                    continue
                ddl = _ddl_for(kind, dialect)
                db.session.execute(text(f"ALTER TABLE users ADD COLUMN {col} {ddl}"))
                added += 1
            if added:
                db.session.commit()
                logger.info("users 表已补齐 %s 个新列", added)
        except Exception as exc:  # pragma: no cover - 迁移失败不应阻断启动
            db.session.rollback()
            logger.warning("users 列补齐跳过（%s）", exc)
    return added


def ensure_content_columns(app) -> int:
    """补齐 content_items 的多语释义列（meaning_en / meaning_yue / example_yue）。

    这是「English 版面零中文」的数据前提：没有 ``meaning_en`` 就无法在英文
    界面给出释义。幂等，老数据不受影响。
    """
    added = 0
    with app.app_context():
        for table, cols in _EXTRA_COLUMNS_BY_TABLE.items():
            try:
                insp = inspect(db.engine)
                if table not in insp.get_table_names():
                    continue
                existing = {c["name"] for c in insp.get_columns(table)}
                dialect = db.engine.dialect.name
                for col, kind in cols.items():
                    if col in existing:
                        continue
                    db.session.execute(
                        text(f"ALTER TABLE {table} ADD COLUMN {col} {_ddl_for(kind, dialect)}"))
                    added += 1
                if added:
                    db.session.commit()
            except Exception as exc:  # pragma: no cover
                db.session.rollback()
                logger.warning("%s 列补齐跳过（%s）", table, exc)
    return added
