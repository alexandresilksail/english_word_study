"""在预览服务的临时库中准备管理员账号，供人工登录预览。

- 复用 preview_server.py 使用的同一个 SQLite 文件（%TEMP%/v5_preview.db）；
- 账号：preview@example.com / Preview123!  （is_admin=True，便于查看管理后台入口）；
- 幂等：已存在则只重置密码。
"""
from __future__ import annotations

import os
import sys
import tempfile

# 保证从项目根导入（本脚本位于 scripts/ 目录）
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_db_path = os.path.join(tempfile.gettempdir(), "v5_preview.db")
os.environ["DATABASE_URL"] = "sqlite:///" + _db_path
os.environ.setdefault("SECRET_KEY", "v5-preview-dev-key")
os.environ.setdefault("FLASK_ENV", "development")

from app import create_app  # noqa: E402
from extensions import db  # noqa: E402
from config import DevelopmentConfig  # noqa: E402
from models import User  # noqa: E402

EMAIL = os.getenv("PREVIEW_EMAIL", "preview@example.com")
PASSWORD = os.getenv("PREVIEW_PASSWORD", "change-me-in-env")

app = create_app(DevelopmentConfig)
with app.app_context():
    db.create_all()
    u = User.query.filter_by(email=EMAIL).first()
    if u is None:
        u = User(email=EMAIL, username="预览账号", is_admin=True)
        db.session.add(u)
    u.is_admin = True
    u.set_password(PASSWORD)
    db.session.commit()
    print(f">> preview user ready: {EMAIL} / {PASSWORD} (is_admin={u.is_admin}, db={_db_path})")
