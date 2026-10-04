"""Flask 扩展单例集中管理（避免在多个蓝图中重复实例化，防止循环导入）。"""
from flask_sqlalchemy import SQLAlchemy
from flask_wtf.csrf import CSRFProtect

db = SQLAlchemy()
csrf = CSRFProtect()
