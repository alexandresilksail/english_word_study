"""应用配置。

所有敏感值与域名均从环境变量读取，禁止写死在代码里。
本地开发可把变量写进 .env（不要提交到 Git）。
"""
from __future__ import annotations

import os
from datetime import timedelta


def _env_bool(name: str, default: bool = False) -> bool:
    raw = (os.environ.get(name) or "").strip().lower()
    if not raw:
        return default
    return raw in ("1", "true", "yes", "on")


def _env_int(name: str, default: int) -> int:
    try:
        return int((os.environ.get(name) or "").strip() or default)
    except ValueError:
        return default


class Config:
    """通用配置：开发 / 生产共用，差异项由子类或环境变量决定。"""

    # ---------------- 基础 ----------------
    # SECRET_KEY 必须从环境变量读取，缺失时（生产）直接启动失败
    SECRET_KEY = os.environ.get("SECRET_KEY") or ""
    APP_TITLE_CN = os.environ.get("APP_TITLE_CN") or "英语单词学习"
    DEBUG = False
    TESTING = False

    # 域名（不要把域名写死在代码里）
    DOMAIN = (os.environ.get("DOMAIN") or "").strip()

    # ---------------- 数据库 ----------------
    # 默认 SQLite（第一版）；将来切 MySQL / PostgreSQL 只需改 DATABASE_URL
    # 例：mysql+pymysql://user:pwd@host:3306/dbname
    #     postgresql+psycopg2://user:pwd@host:5432/dbname
    # 注意：ORM 层不使用任何 SQLite 专有字段，迁移时无需改模型。
    _BASE_DIR = os.path.abspath(os.path.dirname(__file__))
    DATABASE_URL = (os.environ.get("DATABASE_URL") or "").strip() or (
        "sqlite:///" + os.path.join(_BASE_DIR, "instance", "english_word_study.db")
    )
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    SQLALCHEMY_ENGINE_OPTIONS = {
        # SQLite 长连接可能出现 "database is locked"，加超时 + 预检
        "pool_pre_ping": True,
        "connect_args": {"timeout": 30, "check_same_thread": False}
        if DATABASE_URL.startswith("sqlite")
        else {},
    }

    # ---------------- Session 安全 ----------------
    SESSION_COOKIE_NAME = "ews_session"
    SESSION_COOKIE_HTTPONLY = True          # 禁止 JS 读取
    SESSION_COOKIE_SAMESITE = "Lax"         # 防 CSRF
    SESSION_COOKIE_SECURE = False           # 生产（HTTPS）下自动打开
    PERMANENT_SESSION_LIFETIME = timedelta(days=14)
    REMEMBER_COOKIE_DURATION = timedelta(days=14)
    REMEMBER_COOKIE_HTTPONLY = True
    REMEMBER_COOKIE_SAMESITE = "Lax"
    SESSION_REFRESH_EACH_REQUEST = True

    # ---------------- CSRF ----------------
    WTF_CSRF_ENABLED = True
    WTF_CSRF_TIME_LIMIT = 3600 * 6
    WTF_CSRF_SSL_STRICT = False             # HTTPS 反向代理后可关闭 Referer 严格校验

    # ---------------- 登录安全 ----------------
    MAX_LOGIN_ATTEMPTS = _env_int("MAX_LOGIN_ATTEMPTS", 5)        # 连续失败次数上限
    LOGIN_LOCK_MINUTES = _env_int("LOGIN_LOCK_MINUTES", 15)       # 锁定时长（分钟）

    # ---------------- 防爆破：IP 维度限流（V5.9 HIGH-1） ----------------
    # 默认开启（开发 / 生产都生效）。测试环境共用 127.0.0.1，会把计数迅速打满，
    # 导致整批登录被误拦，因此测试配置统一关闭；专门验证限流的用例再用
    # 显式开启的私有配置（配合独立 TEST-NET IP）隔离。
    IP_RATELIMIT_ENABLED = True

    # ---------------- 密码强度 ----------------
    PASSWORD_MIN_LENGTH = 8

    # ---------------- 其它 ----------------
    JSON_AS_ASCII = False
    MAX_CONTENT_LENGTH = 8 * 1024 * 1024
    WORDS_JSON = os.path.join(_BASE_DIR, "app", "data", "words.json")
    ADMIN_BOOTSTRAP_EMAIL = (os.environ.get("ADMIN_EMAIL") or "").strip().lower()
    ADMIN_BOOTSTRAP_PASSWORD = os.environ.get("ADMIN_PASSWORD") or ""

    # ---------------- 媒体（音频 / 图片） ----------------
    # 音频文件不进 GitHub、不进镜像、不进数据库：业务表只存逻辑标识，
    # 真实地址由 media_service 解析。留空 = 用站内 /static（当前生产行为不变）；
    # 将来接对象存储 + CDN 时只需设置这两个变量，无需改任何业务代码。
    MEDIA_PROVIDER = (os.environ.get("MEDIA_PROVIDER") or "local").strip().lower()
    MEDIA_BASE_URL = (os.environ.get("MEDIA_BASE_URL") or "").strip()

    # ---------------- 产品版本（UI / API 展示用） ----------------
    APP_VERSION = os.environ.get("APP_VERSION") or "3.0.0"
    APP_TITLE_EN = os.environ.get("APP_TITLE_EN") or "English Learning Platform"


class DevelopmentConfig(Config):
    DEBUG = True
    SESSION_COOKIE_SECURE = False


class ProductionConfig(Config):
    DEBUG = False
    SESSION_COOKIE_SECURE = _env_bool("SESSION_COOKIE_SECURE", True)
    SESSION_COOKIE_SAMESITE = "Lax"
    PREFERRED_URL_SCHEME = "https"


class TestingConfig(Config):
    TESTING = True
    DEBUG = False
    WTF_CSRF_ENABLED = False
    DATABASE_URL = "sqlite:///:memory:"
    IP_RATELIMIT_ENABLED = False          # 测试共用 127.0.0.1，关闭以免误拦整批登录


CONFIGS = {
    "development": DevelopmentConfig,
    "production": ProductionConfig,
    "testing": TestingConfig,
}


def load_config() -> type[Config]:
    name = (os.environ.get("FLASK_ENV") or os.environ.get("FLASK_CONFIG") or "development").strip().lower()
    return CONFIGS.get(name, DevelopmentConfig)
