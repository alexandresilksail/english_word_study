"""数据模型。

设计原则：
1. 仅使用 SQLAlchemy 跨数据库通用类型（String / Integer / Boolean / DateTime / Text），
   不使用 SQLite 专有写法，保证后续可平滑迁移 MySQL / PostgreSQL。
2. 每张业务表都带 user_id 外键，所有查询必须按 user_id 过滤 —— 用户数据严格隔离。
3. 时间统一使用 UTC naive datetime（server_default=func.now()），不做时区绑定。
"""
from __future__ import annotations

from datetime import datetime, timezone

from flask_login import UserMixin
from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.orm import relationship

from extensions import db


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


# --------------------------------------------------------------------------
# 用户
# --------------------------------------------------------------------------
class User(UserMixin, db.Model):
    __tablename__ = "users"

    id = db.Column(Integer, primary_key=True)
    email = db.Column(String(255), nullable=False, unique=True, index=True)
    username = db.Column(String(64), nullable=False)
    password_hash = db.Column(String(255), nullable=False)   # Werkzeug 哈希，绝不存明文
    is_admin = db.Column(Boolean, nullable=False, default=False, server_default="0")

    created_at = db.Column(DateTime, nullable=False, default=utcnow, server_default=func.now())
    last_login_at = db.Column(DateTime, nullable=True)
    login_count = db.Column(Integer, nullable=False, default=0, server_default="0")

    # 登录失败限制（防爆破）
    failed_logins = db.Column(Integer, nullable=False, default=0, server_default="0")
    locked_until = db.Column(DateTime, nullable=True)

    # 邮箱验证 / 密码重置（增量字段，均为可空，不影响既有用户数据）
    email_verified = db.Column(Boolean, nullable=False, default=False, server_default="0")
    verify_token = db.Column(String(64), nullable=True, index=True)
    reset_token = db.Column(String(64), nullable=True, index=True)
    reset_token_exp = db.Column(DateTime, nullable=True)

    # 关系
    favorites = relationship("Favorite", back_populates="user", cascade="all, delete-orphan", lazy="dynamic")
    wrong_answers = relationship("WrongAnswer", back_populates="user", cascade="all, delete-orphan", lazy="dynamic")
    progress = relationship("UserWordProgress", back_populates="user", cascade="all, delete-orphan", lazy="dynamic")
    test_records = relationship("TestRecord", back_populates="user", cascade="all, delete-orphan", lazy="dynamic")
    study_records = relationship("StudyRecord", back_populates="user", cascade="all, delete-orphan", lazy="dynamic")

    def set_password(self, raw: str) -> None:
        from werkzeug.security import generate_password_hash
        self.password_hash = generate_password_hash(raw, method="pbkdf2:sha256", salt_length=16)

    def check_password(self, raw: str) -> bool:
        from werkzeug.security import check_password_hash
        if not self.password_hash:
            return False
        return check_password_hash(self.password_hash, raw)

    @property
    def is_locked(self) -> bool:
        return bool(self.locked_until and self.locked_until > utcnow())

    def __repr__(self) -> str:
        return f"<User {self.email}>"


# --------------------------------------------------------------------------
# 词库（全局共享，不绑定用户）
# --------------------------------------------------------------------------
class Word(db.Model):
    __tablename__ = "words"

    id = db.Column(Integer, primary_key=True)
    word = db.Column(String(64), nullable=False, unique=True, index=True)
    initial = db.Column(String(1), nullable=False, index=True)       # 首字母，用于 A-Z 浏览
    phonetic_uk = db.Column(String(64), nullable=False, default="")  # 英式 IPA
    pos = db.Column(String(16), nullable=False, default="")          # 词性
    meaning_cn = db.Column(String(512), nullable=False, default="")  # 中文释义
    example_en = db.Column(Text, nullable=False, default="")         # 英文例句
    example_cn = db.Column(Text, nullable=False, default="")         # 中文例句
    audio = db.Column(String(128), nullable=False, default="")       # 本地音频文件名
    level = db.Column(String(64), nullable=False, default="")        # 考试标签
    freq = db.Column(Integer, nullable=False, default=0, server_default="0")

    def __repr__(self) -> str:
        return f"<Word {self.word}>"


# --------------------------------------------------------------------------
# 学习进度（每用户 × 每单词）
# --------------------------------------------------------------------------
class UserWordProgress(db.Model):
    __tablename__ = "user_word_progress"
    __table_args__ = (
        UniqueConstraint("user_id", "word_id", name="uq_progress_user_word"),
        Index("ix_progress_user_status", "user_id", "status"),
    )

    id = db.Column(Integer, primary_key=True)
    user_id = db.Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    word_id = db.Column(Integer, ForeignKey("words.id", ondelete="CASCADE"), nullable=False, index=True)

    status = db.Column(String(16), nullable=False, default="new")    # new / learning / mastered
    correct_count = db.Column(Integer, nullable=False, default=0, server_default="0")
    wrong_count = db.Column(Integer, nullable=False, default=0, server_default="0")
    view_count = db.Column(Integer, nullable=False, default=0, server_default="0")

    first_learned_at = db.Column(DateTime, nullable=True)
    last_studied_at = db.Column(DateTime, nullable=True)
    mastered_at = db.Column(DateTime, nullable=True)

    user = relationship("User", back_populates="progress")
    word = relationship("Word")


# --------------------------------------------------------------------------
# 收藏
# --------------------------------------------------------------------------
class Favorite(db.Model):
    __tablename__ = "favorites"
    __table_args__ = (UniqueConstraint("user_id", "word_id", name="uq_fav_user_word"),)

    id = db.Column(Integer, primary_key=True)
    user_id = db.Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    word_id = db.Column(Integer, ForeignKey("words.id", ondelete="CASCADE"), nullable=False, index=True)
    created_at = db.Column(DateTime, nullable=False, default=utcnow, server_default=func.now())

    user = relationship("User", back_populates="favorites")
    word = relationship("Word")


# --------------------------------------------------------------------------
# 错题本
# --------------------------------------------------------------------------
class WrongAnswer(db.Model):
    __tablename__ = "wrong_answers"
    __table_args__ = (UniqueConstraint("user_id", "word_id", name="uq_wrong_user_word"),
                      Index("ix_wrong_user_recent", "user_id", "last_wrong_at"),)

    id = db.Column(Integer, primary_key=True)
    user_id = db.Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    word_id = db.Column(Integer, ForeignKey("words.id", ondelete="CASCADE"), nullable=False, index=True)
    wrong_count = db.Column(Integer, nullable=False, default=1, server_default="1")
    last_answer = db.Column(String(128), nullable=False, default="")   # 最近一次答错的内容
    first_wrong_at = db.Column(DateTime, nullable=False, default=utcnow, server_default=func.now())
    last_wrong_at = db.Column(DateTime, nullable=False, default=utcnow, server_default=func.now())

    user = relationship("User", back_populates="wrong_answers")
    word = relationship("Word")


# --------------------------------------------------------------------------
# 测试记录（一次测试会话）
# --------------------------------------------------------------------------
class TestRecord(db.Model):
    __tablename__ = "test_records"
    __table_args__ = (Index("ix_test_user_created", "user_id", "created_at"),)

    id = db.Column(Integer, primary_key=True)
    user_id = db.Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    mode = db.Column(String(16), nullable=False, default="choice")   # choice / listen / spell / zh_en
    total = db.Column(Integer, nullable=False, default=0, server_default="0")
    score = db.Column(Integer, nullable=False, default=0, server_default="0")
    duration_sec = db.Column(Integer, nullable=False, default=0, server_default="0")
    created_at = db.Column(DateTime, nullable=False, default=utcnow, server_default=func.now())

    user = relationship("User", back_populates="test_records")

    @property
    def percent(self) -> int:
        return round(self.score * 100 / self.total) if self.total else 0


# --------------------------------------------------------------------------
# 学习记录（每道题的作答明细）
# --------------------------------------------------------------------------
class StudyRecord(db.Model):
    __tablename__ = "study_records"
    __table_args__ = (Index("ix_study_user_created", "user_id", "created_at"),)

    id = db.Column(Integer, primary_key=True)
    user_id = db.Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    word_id = db.Column(Integer, ForeignKey("words.id", ondelete="SET NULL"), nullable=True)
    action = db.Column(String(24), nullable=False, default="view")   # view / answer / favorite / test
    mode = db.Column(String(16), nullable=False, default="")
    is_correct = db.Column(Boolean, nullable=True)                   # None=非答题行为
    created_at = db.Column(DateTime, nullable=False, default=utcnow, server_default=func.now())

    user = relationship("User", back_populates="study_records")
    word = relationship("Word")


TEST_MODE_LABELS = {
    "choice": "英文 → 中文",
    "zh_en": "中文 → 英文",
    "listen": "听音测试",
    "spell": "拼写测试",
}
