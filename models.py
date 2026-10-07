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

    # 该账号是否通过「无密码 · 邮箱验证码」方式创建（密码为随机值，用户并不知晓）
    is_passwordless = db.Column(Boolean, nullable=False, default=False, server_default="0")

    # V5：界面语言偏好（zh / en / None=双语自动）。登录用户存这里，未登录走 Cookie
    preferred_lang = db.Column(String(8), nullable=True)
    notify_email = db.Column(Boolean, nullable=False, default=True, server_default="1")

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

    def set_unusable_password(self) -> None:
        """无密码账号：写入一个随机且无人知晓的哈希，使密码登录自然失效。

        这样既满足 password_hash 非空的既有约束，也保证此类账号只能凭邮箱验证码登录。
        """
        import secrets

        self.set_password(secrets.token_urlsafe(32))
        self.is_passwordless = True

    @property
    def is_locked(self) -> bool:
        return bool(self.locked_until and self.locked_until > utcnow())

    def __repr__(self) -> str:
        return f"<User {self.email}>"


# --------------------------------------------------------------------------
# 邮箱验证码（一次性登录 / 注册码）
# --------------------------------------------------------------------------
class EmailCode(db.Model):
    """邮箱验证码。

    独立于 users 之外单独建表的原因：
    - 注册时用户记录尚不存在，验证码无处安放
    - 支持跨设备（手机上收码、电脑上提交）
    - 便于统一限流与清理

    安全：明文验证码只出现在邮件里，库中仅保存哈希；校验成功后立即删除（一次性）。
    """

    __tablename__ = "email_codes"
    __table_args__ = (
        Index("ix_email_codes_email_purpose", "email", "purpose"),
    )

    id = db.Column(Integer, primary_key=True)
    email = db.Column(String(255), nullable=False, index=True)
    code_hash = db.Column(String(255), nullable=False)          # 哈希，绝不存明文
    purpose = db.Column(String(16), nullable=False)             # 'login' | 'register'
    created_at = db.Column(DateTime, nullable=False, default=utcnow, server_default=func.now())
    expires_at = db.Column(DateTime, nullable=False, index=True)
    attempts = db.Column(Integer, nullable=False, default=0, server_default="0")  # 错误次数
    consumed_at = db.Column(DateTime, nullable=True)            # 已使用则标记
    ip = db.Column(String(64), nullable=True)                   # 仅用于审计/限流

    def __repr__(self) -> str:
        return f"<EmailCode {self.email} {self.purpose}>"

    @property
    def is_expired(self) -> bool:
        return self.expires_at < utcnow()


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
# V5：词库分级元数据（CEFR / 难度 / 类别 / 单元 / 学习语言）
#
# 不动 words 表本身（生产风险），用独立表挂分级；学习语言 learning_language
# 为未来粤语/日语/西语等课程预留 —— 同一套学习引擎，只换数据行。
# --------------------------------------------------------------------------
class WordMeta(db.Model):
    __tablename__ = "word_meta"
    __table_args__ = (UniqueConstraint("word_id", "learning_language", name="uq_meta_word_lang"),)

    id = db.Column(Integer, primary_key=True)
    word_id = db.Column(Integer, ForeignKey("words.id", ondelete="CASCADE"),
                       nullable=False, unique=True, index=True)
    learning_language = db.Column(String(16), nullable=False, default="en", index=True)  # en/yue/ja/es…
    cefr_level = db.Column(String(4), nullable=False, default="A1", index=True)          # A1..C2
    unit_no = db.Column(Integer, nullable=False, default=1, server_default="1")           # 1..3
    difficulty = db.Column(Integer, nullable=False, default=1, server_default="1")        # 1..6 ≈ CEFR 序+1
    category = db.Column(String(32), nullable=False, default="general")

    word = relationship("Word")


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


# ==========================================================================
# V3.0 新增：游戏化 / 技能 / 媒体资产 / Podcast 架构预留
#
# 设计约束（面向 Web + 微信小程序 + Android + macOS + Podcast/Audio）：
# 1. 全是**新增表**，不 ALTER 任何既有表，不删除任何历史数据 —— 老用户零影响。
# 2. 只用 SQLAlchemy 通用类型，SQLite → PostgreSQL 迁移无需改模型。
# 3. 音频/图片一律通过 MediaAsset 间接引用，业务表不存死链接，
#    将来切对象存储 + CDN 只需改 media_service 的解析函数。
# ==========================================================================

class UserStat(db.Model):
    """用户游戏化总览：XP / 等级 / 连续学习天数 / 每日目标。

    单独建表而不是往 users 加列的原因：users 已是线上生产表，
    任何 ALTER 都有风险；新表由 create_all() 建立，纯增量、可回滚。
    """

    __tablename__ = "user_stats"

    user_id = db.Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    xp = db.Column(Integer, nullable=False, default=0, server_default="0")
    level = db.Column(Integer, nullable=False, default=1, server_default="1")
    streak_days = db.Column(Integer, nullable=False, default=0, server_default="0")
    best_streak = db.Column(Integer, nullable=False, default=0, server_default="0")
    last_active_date = db.Column(String(10), nullable=True)      # YYYY-MM-DD（UTC）

    daily_goal = db.Column(Integer, nullable=False, default=20, server_default="20")
    today_done = db.Column(Integer, nullable=False, default=0, server_default="0")
    today_date = db.Column(String(10), nullable=True)

    total_games = db.Column(Integer, nullable=False, default=0, server_default="0")
    updated_at = db.Column(DateTime, nullable=False, default=utcnow, server_default=func.now())

    user = relationship("User")

    @property
    def today_goal_percent(self) -> int:
        if not self.daily_goal:
            return 0
        return min(100, round(self.today_done * 100 / self.daily_goal))


class GameRecord(db.Model):
    """小游戏一次游玩记录（可回放、可统计）。"""

    __tablename__ = "game_records"
    __table_args__ = (Index("ix_game_user_played", "user_id", "played_at"),
                      Index("ix_game_user_key", "user_id", "game_key"))

    id = db.Column(Integer, primary_key=True)
    user_id = db.Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    game_key = db.Column(String(24), nullable=False)     # word_match / speed_quiz / listening / word_builder
    score = db.Column(Integer, nullable=False, default=0, server_default="0")
    xp_earned = db.Column(Integer, nullable=False, default=0, server_default="0")
    correct = db.Column(Integer, nullable=False, default=0, server_default="0")
    total = db.Column(Integer, nullable=False, default=0, server_default="0")
    max_combo = db.Column(Integer, nullable=False, default=0, server_default="0")
    duration_sec = db.Column(Integer, nullable=False, default=0, server_default="0")
    played_at = db.Column(DateTime, nullable=False, default=utcnow, server_default=func.now())

    user = relationship("User")

    @property
    def accuracy(self) -> int:
        return round(self.correct * 100 / self.total) if self.total else 0


class GameStat(db.Model):
    """小游戏累计统计（每个用户 × 每个游戏一行，用于 Best Score / Games Played）。"""

    __tablename__ = "game_stats"
    __table_args__ = (UniqueConstraint("user_id", "game_key", name="uq_gamestat_user_game"),)

    id = db.Column(Integer, primary_key=True)
    user_id = db.Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    game_key = db.Column(String(24), nullable=False)
    best_score = db.Column(Integer, nullable=False, default=0, server_default="0")
    plays = db.Column(Integer, nullable=False, default=0, server_default="0")
    total_xp = db.Column(Integer, nullable=False, default=0, server_default="0")
    total_correct = db.Column(Integer, nullable=False, default=0, server_default="0")
    total_questions = db.Column(Integer, nullable=False, default=0, server_default="0")
    best_combo = db.Column(Integer, nullable=False, default=0, server_default="0")
    last_played_at = db.Column(DateTime, nullable=True)

    user = relationship("User")

    @property
    def accuracy(self) -> int:
        return round(self.total_correct * 100 / self.total_questions) if self.total_questions else 0


class SkillStat(db.Model):
    """六项技能（Vocabulary / Listening / Reading / Grammar / Speaking / Writing）进度。

    本阶段只有 Vocabulary 真正产出数据；其余技能先建入口与统计骨架，
    后续接入真实内容时直接复用同一张表，无需再迁移。
    """

    __tablename__ = "skill_stats"
    __table_args__ = (UniqueConstraint("user_id", "skill", name="uq_skillstat_user_skill"),)

    id = db.Column(Integer, primary_key=True)
    user_id = db.Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    skill = db.Column(String(16), nullable=False)          # vocabulary/listening/...
    xp = db.Column(Integer, nullable=False, default=0, server_default="0")
    items = db.Column(Integer, nullable=False, default=0, server_default="0")   # 完成的条目数
    minutes = db.Column(Integer, nullable=False, default=0, server_default="0")
    last_at = db.Column(DateTime, nullable=True)

    user = relationship("User")


class MediaAsset(db.Model):
    """媒体资产（音频 / 封面图）的**间接引用**。

    音频文件将来不进 GitHub、不进 Docker 镜像、不进数据库 —— 只存
    provider + key，实际地址由 media_service 解析（本地 / OSS / COS / S3 + CDN）。
    业务表引用 asset_id，迁移到对象存储时只需改解析函数，业务代码零改动。
    """

    __tablename__ = "media_assets"
    __table_args__ = (Index("ix_asset_kind_provider", "kind", "provider"),)

    id = db.Column(Integer, primary_key=True)
    kind = db.Column(String(24), nullable=False)             # word_audio / podcast_audio / cover
    provider = db.Column(String(16), nullable=False, default="local")  # local / oss / cos / s3
    key = db.Column(String(512), nullable=False)             # 文件名 或 对象键
    url = db.Column(String(1024), nullable=True)             # 外链兜底（可空）
    cdn_url = db.Column(String(1024), nullable=True)         # 未来 CDN 地址（可空）
    mime = db.Column(String(64), nullable=True)
    bytes = db.Column(Integer, nullable=True)
    duration_sec = db.Column(Integer, nullable=True)
    checksum = db.Column(String(64), nullable=True)
    created_at = db.Column(DateTime, nullable=False, default=utcnow, server_default=func.now())


# --------------------------------------------------------------------------
# Podcast 架构预留（本阶段只建模型，不实现完整系统）
#
# 未来一条 Podcast 的转换链路：
#   Audio → Transcript → Vocabulary → Grammar → Listening → Quiz → Speaking → AI Tutor
# 下面的表就是这条链路的落点；episode 通过 media_asset_id 引用音频，
# 不把音频路径写死在任何业务逻辑里。
# --------------------------------------------------------------------------
class PodcastChannel(db.Model):
    __tablename__ = "podcast_channels"

    id = db.Column(Integer, primary_key=True)
    slug = db.Column(String(64), nullable=False, unique=True, index=True)
    title = db.Column(String(255), nullable=False)
    subtitle = db.Column(String(512), nullable=False, default="")
    description = db.Column(Text, nullable=False, default="")
    language = db.Column(String(16), nullable=False, default="en")
    level = db.Column(String(16), nullable=False, default="B1")     # A1/A2/B1/B2/C1
    cover_asset_id = db.Column(Integer, ForeignKey("media_assets.id", ondelete="SET NULL"), nullable=True)
    created_at = db.Column(DateTime, nullable=False, default=utcnow, server_default=func.now())

    episodes = relationship("PodcastEpisode", back_populates="channel",
                            cascade="all, delete-orphan", lazy="dynamic")


class PodcastEpisode(db.Model):
    __tablename__ = "podcast_episodes"
    __table_args__ = (Index("ix_episode_channel_pub", "channel_id", "published_at"),)

    id = db.Column(Integer, primary_key=True)
    channel_id = db.Column(Integer, ForeignKey("podcast_channels.id", ondelete="CASCADE"),
                           nullable=False, index=True)
    slug = db.Column(String(128), nullable=False, unique=True, index=True)
    title = db.Column(String(255), nullable=False)
    summary = db.Column(Text, nullable=False, default="")
    level = db.Column(String(16), nullable=False, default="B1")
    duration_sec = db.Column(Integer, nullable=False, default=0, server_default="0")
    media_asset_id = db.Column(Integer, ForeignKey("media_assets.id", ondelete="SET NULL"), nullable=True)
    published_at = db.Column(DateTime, nullable=True)
    status = db.Column(String(16), nullable=False, default="draft")   # draft/published
    created_at = db.Column(DateTime, nullable=False, default=utcnow, server_default=func.now())

    channel = relationship("PodcastChannel", back_populates="episodes")
    transcript = relationship("PodcastTranscript", back_populates="episode", uselist=False,
                              cascade="all, delete-orphan")
    vocabularies = relationship("PodcastVocabulary", back_populates="episode",
                                cascade="all, delete-orphan", lazy="dynamic")
    grammars = relationship("PodcastGrammar", back_populates="episode",
                            cascade="all, delete-orphan", lazy="dynamic")
    quizzes = relationship("PodcastQuiz", back_populates="episode",
                           cascade="all, delete-orphan", lazy="dynamic")


class PodcastTranscript(db.Model):
    """字幕 / 文稿：按时间轴切段，供 Listening、跟读、精听复用。"""

    __tablename__ = "podcast_transcripts"

    id = db.Column(Integer, primary_key=True)
    episode_id = db.Column(Integer, ForeignKey("podcast_episodes.id", ondelete="CASCADE"),
                           nullable=False, unique=True, index=True)
    language = db.Column(String(16), nullable=False, default="en")
    content = db.Column(Text, nullable=False, default="")           # 纯文本整稿
    segments = db.Column(Text, nullable=False, default="[]")        # JSON: [{start,end,text}]
    translation = db.Column(Text, nullable=False, default="")       # 中文翻译
    created_at = db.Column(DateTime, nullable=False, default=utcnow, server_default=func.now())

    episode = relationship("PodcastEpisode", back_populates="transcript")


class PodcastVocabulary(db.Model):
    """Podcast 生词：可关联到主词库 Word，也可独立存在。"""

    __tablename__ = "podcast_vocabularies"
    __table_args__ = (Index("ix_pvocab_episode", "episode_id"),)

    id = db.Column(Integer, primary_key=True)
    episode_id = db.Column(Integer, ForeignKey("podcast_episodes.id", ondelete="CASCADE"),
                           nullable=False, index=True)
    word_id = db.Column(Integer, ForeignKey("words.id", ondelete="SET NULL"), nullable=True)
    surface = db.Column(String(64), nullable=False)         # 文中出现的形态
    meaning_cn = db.Column(String(512), nullable=False, default="")
    timestamp_sec = db.Column(Integer, nullable=True)       # 在音频中出现的位置

    episode = relationship("PodcastEpisode", back_populates="vocabularies")
    word = relationship("Word")


class PodcastGrammar(db.Model):
    __tablename__ = "podcast_grammars"
    __table_args__ = (Index("ix_pgrammar_episode", "episode_id"),)

    id = db.Column(Integer, primary_key=True)
    episode_id = db.Column(Integer, ForeignKey("podcast_episodes.id", ondelete="CASCADE"),
                           nullable=False, index=True)
    point = db.Column(String(255), nullable=False)          # 语法点，如 Present Perfect
    explanation = db.Column(Text, nullable=False, default="")
    example = db.Column(Text, nullable=False, default="")

    episode = relationship("PodcastEpisode", back_populates="grammars")


class PodcastQuiz(db.Model):
    """由 Podcast 自动或人工生成的听力理解题。"""

    __tablename__ = "podcast_quizzes"
    __table_args__ = (Index("ix_pquiz_episode", "episode_id"),)

    id = db.Column(Integer, primary_key=True)
    episode_id = db.Column(Integer, ForeignKey("podcast_episodes.id", ondelete="CASCADE"),
                           nullable=False, index=True)
    question = db.Column(Text, nullable=False, default="")
    options = db.Column(Text, nullable=False, default="[]")     # JSON 数组
    answer_index = db.Column(Integer, nullable=False, default=0, server_default="0")
    kind = db.Column(String(24), nullable=False, default="listening")  # listening/vocab/grammar
    timestamp_sec = db.Column(Integer, nullable=True)

    episode = relationship("PodcastEpisode", back_populates="quizzes")


# 六项技能的展示元信息（顺序固定，前端与 API 共用同一份，避免散落各处）
SKILLS = [
    {"key": "vocabulary", "icon": "book", "color": "violet", "ready": True},
    {"key": "listening", "icon": "headphones", "color": "sky", "ready": False},
    {"key": "reading", "icon": "article", "color": "amber", "ready": False},
    {"key": "grammar", "icon": "puzzle", "color": "rose", "ready": False},
    {"key": "speaking", "icon": "mic", "color": "emerald", "ready": False},
    {"key": "writing", "icon": "pen", "color": "indigo", "ready": False},
]

GAMES = [
    {"key": "word_match", "icon": "link", "color": "violet",
     "xp": 20, "desc": "单词与释义配对，越快分越高"},
    {"key": "speed_quiz", "icon": "bolt", "color": "amber",
     "xp": 25, "desc": "60 秒限时抢答，连对有加成"},
    {"key": "listening_challenge", "icon": "headphones", "color": "sky",
     "xp": 20, "desc": "听英式发音，选出正确单词"},
    {"key": "word_builder", "icon": "puzzle", "color": "emerald",
     "xp": 25, "desc": "打乱的字母，拼回正确单词"},
]


# ==========================================================================
# V5.0：AI 多语言分级学习平台核心模型
#
# 核心关系：
#   Language → Course → Level → Unit → Lesson → Content → Practice → Quiz → Review
#
# 设计约束（沿用项目既有约定）：
# 1. 全部新增表，不 ALTER 任何旧表；SQLite → PostgreSQL 只用通用类型。
# 2. 旧 2000 词数据保留为历史/测试数据，不再是产品核心定位。
# 3. ReviewSchedule 走 Leitner 间隔（1/3/7/14/30 天），答错缩间隔、连对延间隔。
# ==========================================================================

class LearningLanguage(db.Model):
    """学习课程目录（English / 粤语 / 未来日语/西语…）。"""
    __tablename__ = "learning_languages"

    id = db.Column(Integer, primary_key=True)
    code = db.Column(String(16), nullable=False, unique=True, index=True)   # en / yue / ja …
    name_zh = db.Column(String(64), nullable=False)
    name_en = db.Column(String(64), nullable=False)
    flag = db.Column(String(8), nullable=False, default="🌐")
    sort = db.Column(Integer, nullable=False, default=0)
    ready = db.Column(Boolean, nullable=False, default=True, server_default="1")


class Course(db.Model):
    """课程：一门语言 × 一个 CEFR 等级。"""
    __tablename__ = "courses"
    __table_args__ = (UniqueConstraint("language_code", "cefr_level", name="uq_course_lang_level"),)

    id = db.Column(Integer, primary_key=True)
    language_code = db.Column(String(16), nullable=False, index=True)
    cefr_level = db.Column(String(8), nullable=False, index=True)        # Pre-A1/A1..C2
    title_zh = db.Column(String(128), nullable=False)
    title_en = db.Column(String(128), nullable=False)
    description = db.Column(Text, nullable=False, default="")
    color = db.Column(String(16), nullable=False, default="violet")
    sort = db.Column(Integer, nullable=False, default=0)


class Unit(db.Model):
    __tablename__ = "units"
    __table_args__ = (UniqueConstraint("course_id", "no", name="uq_unit_course_no"),)

    id = db.Column(Integer, primary_key=True)
    course_id = db.Column(Integer, ForeignKey("courses.id", ondelete="CASCADE"),
                          nullable=False, index=True)
    no = db.Column(Integer, nullable=False)
    title_zh = db.Column(String(128), nullable=False)
    title_en = db.Column(String(128), nullable=False)
    emoji = db.Column(String(8), nullable=False, default="📘")

    lessons = relationship("Lesson", back_populates="unit",
                           cascade="all, delete-orphan", order_by="Lesson.no")


class Lesson(db.Model):
    __tablename__ = "lessons"
    __table_args__ = (UniqueConstraint("unit_id", "no", name="uq_lesson_unit_no"),)

    id = db.Column(Integer, primary_key=True)
    unit_id = db.Column(Integer, ForeignKey("units.id", ondelete="CASCADE"),
                        nullable=False, index=True)
    no = db.Column(Integer, nullable=False)
    kind = db.Column(String(16), nullable=False, default="vocabulary")
    # vocabulary / phrase / sentence / grammar / listening / speaking / quiz / review
    title_zh = db.Column(String(128), nullable=False)
    title_en = db.Column(String(128), nullable=False)

    unit = relationship("Unit", back_populates="lessons")
    contents = relationship("ContentItem", back_populates="lesson",
                            cascade="all, delete-orphan", order_by="ContentItem.id")


class ContentItem(db.Model):
    """学习内容：词条 / 词组 / 句子 / 语法点（统一一张表，kind 区分）。"""
    __tablename__ = "content_items"
    __table_args__ = (Index("ix_content_lesson_kind", "lesson_id", "kind"),)

    id = db.Column(Integer, primary_key=True)
    lesson_id = db.Column(Integer, ForeignKey("lessons.id", ondelete="CASCADE"),
                          nullable=False, index=True)
    kind = db.Column(String(16), nullable=False, default="vocabulary")
    # 词面 / 词组 / 句子 / 语法标题
    surface = db.Column(String(255), nullable=False)
    phonetic = db.Column(String(128), nullable=False, default="")       # IPA / 粤拼 jyutping
    pos = db.Column(String(16), nullable=False, default="")
    meaning_cn = db.Column(String(512), nullable=False, default="")
    # V5.1：English 版面必须能给出英文释义，绝不把中文端给英文用户
    meaning_en = db.Column(String(512), nullable=False, default="")
    meaning_yue = db.Column(String(512), nullable=False, default="")
    example_en = db.Column(Text, nullable=False, default="")
    example_cn = db.Column(Text, nullable=False, default="")
    example_yue = db.Column(Text, nullable=False, default="")
    topic = db.Column(String(32), nullable=False, default="general")
    difficulty = db.Column(Integer, nullable=False, default=1, server_default="1")
    audio = db.Column(String(128), nullable=False, default="")

    lesson = relationship("Lesson", back_populates="contents")


class ReviewItem(db.Model):
    """抗遗忘复习项（Leitner）。挂在 user × content_item 上。"""
    __tablename__ = "review_items"
    __table_args__ = (UniqueConstraint("user_id", "content_id", name="uq_review_user_content"),
                      Index("ix_review_user_due", "user_id", "next_review_at"))

    id = db.Column(Integer, primary_key=True)
    user_id = db.Column(Integer, ForeignKey("users.id", ondelete="CASCADE"),
                        nullable=False, index=True)
    content_id = db.Column(Integer, ForeignKey("content_items.id", ondelete="CASCADE"),
                           nullable=False, index=True)
    box = db.Column(Integer, nullable=False, default=1, server_default="1")   # Leitner 1..5
    first_learned_at = db.Column(DateTime, nullable=False, default=utcnow, server_default=func.now())
    last_reviewed_at = db.Column(DateTime, nullable=True)
    next_review_at = db.Column(DateTime, nullable=False, default=utcnow, index=True)
    review_count = db.Column(Integer, nullable=False, default=0, server_default="0")
    correct_streak = db.Column(Integer, nullable=False, default=0, server_default="0")
    wrong_count = db.Column(Integer, nullable=False, default=0, server_default="0")


class ContentMastery(db.Model):
    """每用户 × 每内容条目的掌握度（V5.5 引入，V5.6 升级为统一 0-4 计算）。

    与 ReviewItem（Leitner 抗遗忘队列）解耦：
    - ReviewItem 负责「何时复习」（next_review_at）；
    - ContentMastery 负责「掌握到什么程度」（level 0-4 + 计数 + 弱项）。

    写路径由 services/mastery_service.py 统一收口；V5.5 仅落原始计数，
    V5.6 的 mastery_service 扩展会纳入 quiz / unit test 分数计算统一 level。
    """

    __tablename__ = "content_mastery"
    __table_args__ = (
        UniqueConstraint("user_id", "content_id", name="uq_cm_user_content"),
        Index("ix_cm_user_level", "user_id", "level"),
        Index("ix_cm_user_weak", "user_id", "weak"),
    )

    id = db.Column(Integer, primary_key=True)
    user_id = db.Column(Integer, ForeignKey("users.id", ondelete="CASCADE"),
                        nullable=False, index=True)
    content_id = db.Column(Integer, ForeignKey("content_items.id", ondelete="CASCADE"),
                           nullable=False, index=True)

    # 统一掌握度 0-4：0 New / 1 Learning / 2 Familiar / 3 Strong / 4 Mastered
    # （V5.5 的初步规则在 mastery_service.level_from_record；V5.6 会扩展）
    level = db.Column(Integer, nullable=False, default=0, server_default="0")

    correct_count = db.Column(Integer, nullable=False, default=0, server_default="0")
    wrong_count = db.Column(Integer, nullable=False, default=0, server_default="0")
    review_count = db.Column(Integer, nullable=False, default=0, server_default="0")
    streak = db.Column(Integer, nullable=False, default=0, server_default="0")

    # 最近一次测验 / 单元测试得分（百分比），供 V5.6 统一 level 计算
    quiz_score = db.Column(Integer, nullable=False, default=0, server_default="0")
    unit_test_score = db.Column(Integer, nullable=False, default=0, server_default="0")

    # 弱项：答错或单元测验失分即标记；reasons 存 JSON 明细（kind / given / expected）
    weak = db.Column(Boolean, nullable=False, default=False, server_default="0")
    weak_reasons = db.Column(Text, nullable=False, default="[]")

    first_learned_at = db.Column(DateTime, nullable=True)
    last_seen_at = db.Column(DateTime, nullable=True)
    next_review_at = db.Column(DateTime, nullable=True)
    updated_at = db.Column(DateTime, nullable=False, default=utcnow, server_default=func.now())


class UserOnboarding(db.Model):
    """新用户画像：年龄组 / 教育 / 当前水平 / 学习目标。"""
    __tablename__ = "user_onboarding"

    user_id = db.Column(Integer, ForeignKey("users.id", ondelete="CASCADE"),
                        primary_key=True)
    age_group = db.Column(String(16), nullable=False, default="adult")
    education = db.Column(String(16), nullable=False, default="adult")
    current_level = db.Column(String(8), nullable=False, default="A1")
    goal = db.Column(String(32), nullable=False, default="daily")
    completed_at = db.Column(DateTime, nullable=True)


class Subscription(db.Model):
    """商业化预留：free / premium / pro（第一阶段 Mock，不接真实支付）。"""
    __tablename__ = "subscriptions"

    user_id = db.Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    plan = db.Column(String(16), nullable=False, default="free", server_default="free")
    status = db.Column(String(16), nullable=False, default="active", server_default="active")
    started_at = db.Column(DateTime, nullable=True)
    expires_at = db.Column(DateTime, nullable=True)


# ==========================================================================
# V5.2：单元测验（Unit Test）—— 规格 §5
#
#   Language → Course → Unit → Lesson → **Quiz → QuizQuestion** → Attempt → Unlock
#
# 与项目既有约定一致：只新增表、不改旧表；字段只用通用类型，
# SQLite → PostgreSQL 无需改模型。题目由该 Unit 的 ContentItem 自动生成，
# 新增内容后重建测验即可，无��手写题库。
# ==========================================================================

class Quiz(db.Model):
    """一个 Unit 一套单元测验（幂等生成）。"""
    __tablename__ = "quizzes"

    id = db.Column(Integer, primary_key=True)
    unit_id = db.Column(Integer, ForeignKey("units.id", ondelete="CASCADE"),
                        nullable=False, unique=True, index=True)
    title_zh = db.Column(String(128), nullable=False, default="单元测验")
    title_en = db.Column(String(128), nullable=False, default="Unit Test")
    #: 及格线（正确率百分比）；达到即判定 Mastered 并解锁下一单元
    pass_score = db.Column(Integer, nullable=False, default=70, server_default="70")
    question_count = db.Column(Integer, nullable=False, default=0, server_default="0")
    created_at = db.Column(DateTime, nullable=False, default=utcnow, server_default=func.now())

    questions = relationship("QuizQuestion", back_populates="quiz",
                             cascade="all, delete-orphan", order_by="QuizQuestion.no")


class QuizQuestion(db.Model):
    """单元测验题目：题干 + 4 个选项（中英双语）+ 正确下标 + 错题讲解。

    ``options`` 存 JSON：``[{"en": "...", "zh": "..."}, ...]``。
    English 版面只渲染 ``en`` 侧 —— 由 ``localization.pick`` 保证绝不把中文端给英文用户。
    """
    __tablename__ = "quiz_questions"
    __table_args__ = (Index("ix_qq_quiz_no", "quiz_id", "no"),)

    id = db.Column(Integer, primary_key=True)
    quiz_id = db.Column(Integer, ForeignKey("quizzes.id", ondelete="CASCADE"),
                        nullable=False, index=True)
    no = db.Column(Integer, nullable=False, default=0, server_default="0")
    kind = db.Column(String(16), nullable=False, default="vocabulary")
    # vocabulary / grammar / listening / sentence
    prompt_en = db.Column(Text, nullable=False, default="")
    prompt_zh = db.Column(Text, nullable=False, default="")
    options = db.Column(Text, nullable=False, default="[]")
    answer_index = db.Column(Integer, nullable=False, default=0, server_default="0")
    explanation_en = db.Column(Text, nullable=False, default="")
    explanation_zh = db.Column(Text, nullable=False, default="")
    content_id = db.Column(Integer, ForeignKey("content_items.id", ondelete="SET NULL"),
                           nullable=True, index=True)

    quiz = relationship("Quiz", back_populates="questions")


class UnitTestAttempt(db.Model):
    """一次单元测验的作答结果（§5 要求的 score / accuracy / time / mistakes / weak_areas）。"""
    __tablename__ = "unit_test_attempts"
    __table_args__ = (Index("ix_attempt_user_unit", "user_id", "unit_id"),
                      Index("ix_attempt_user_done", "user_id", "completed_at"))

    id = db.Column(Integer, primary_key=True)
    user_id = db.Column(Integer, ForeignKey("users.id", ondelete="CASCADE"),
                        nullable=False, index=True)
    unit_id = db.Column(Integer, ForeignKey("units.id", ondelete="CASCADE"),
                        nullable=False, index=True)
    quiz_id = db.Column(Integer, ForeignKey("quizzes.id", ondelete="SET NULL"), nullable=True)

    score = db.Column(Integer, nullable=False, default=0, server_default="0")        # 答对题数
    total = db.Column(Integer, nullable=False, default=0, server_default="0")
    accuracy = db.Column(Integer, nullable=False, default=0, server_default="0")      # 正确率 %
    duration_sec = db.Column(Integer, nullable=False, default=0, server_default="0")
    mistakes = db.Column(Text, nullable=False, default="[]")     # JSON [{question_id, kind, given, correct}]
    weak_areas = db.Column(Text, nullable=False, default="[]")   # JSON ["vocabulary", …]
    passed = db.Column(Boolean, nullable=False, default=False, server_default="0")
    completed_at = db.Column(DateTime, nullable=False, default=utcnow, server_default=func.now())


class UnitProgress(db.Model):
    """单元掌握状态 —— 承载「通过 → 解锁下一单元」的联动（§5）。"""
    __tablename__ = "unit_progress"
    __table_args__ = (UniqueConstraint("user_id", "unit_id", name="uq_unitprog_user_unit"),)

    id = db.Column(Integer, primary_key=True)
    user_id = db.Column(Integer, ForeignKey("users.id", ondelete="CASCADE"),
                        nullable=False, index=True)
    unit_id = db.Column(Integer, ForeignKey("units.id", ondelete="CASCADE"),
                        nullable=False, index=True)
    status = db.Column(String(16), nullable=False, default="available")  # locked/available/mastered
    best_score = db.Column(Integer, nullable=False, default=0, server_default="0")
    attempts = db.Column(Integer, nullable=False, default=0, server_default="0")
    passed_at = db.Column(DateTime, nullable=True)
    updated_at = db.Column(DateTime, nullable=False, default=utcnow, server_default=func.now())


# ==========================================================================
# V5.2：等级主数据 + 商业化链路 —— 规格 §17 / §19
#
# 商业化原先只剩 subscriptions 一张表，无法承载「下单 → 支付 → 开通」的链路，
# 因此补齐 levels / products / orders / payments 四张表。
# 支付本身为 **Mock**（第一阶段不接真实网关），但表结构与状态机按真实链路设计，
# 将来接支付宝/微信时只需替换 payment_service 的 provider 实现。
# ==========================================================================

class Level(db.Model):
    """CEFR 等级主数据 —— 等级目录的唯一来源（此前只存在于 learning_path 的常量里）。"""
    __tablename__ = "levels"

    id = db.Column(Integer, primary_key=True)
    code = db.Column(String(8), nullable=False, unique=True, index=True)   # Pre-A1 … C2
    order = db.Column(Integer, nullable=False, default=0, server_default="0")
    name_zh = db.Column(String(64), nullable=False, default="")
    name_en = db.Column(String(64), nullable=False, default="")
    color = db.Column(String(16), nullable=False, default="violet")
    emoji = db.Column(String(8), nullable=False, default="🌱")
    units = db.Column(Integer, nullable=False, default=3, server_default="3")


class Product(db.Model):
    """商品（订阅套餐）。价格一律以**最小货币单位**存整数，避免浮点误差。"""
    __tablename__ = "products"

    id = db.Column(Integer, primary_key=True)
    code = db.Column(String(32), nullable=False, unique=True, index=True)  # monthly/yearly/lifetime
    name_zh = db.Column(String(64), nullable=False, default="")
    name_en = db.Column(String(64), nullable=False, default="")
    description = db.Column(Text, nullable=False, default="")
    price_cents = db.Column(Integer, nullable=False, default=0, server_default="0")
    currency = db.Column(String(8), nullable=False, default="CNY")
    period = db.Column(String(16), nullable=False, default="month")        # month/year/once
    active = db.Column(Boolean, nullable=False, default=True, server_default="1")
    sort = db.Column(Integer, nullable=False, default=0, server_default="0")


class Order(db.Model):
    """订单：一次购买意图。status 走 pending → paid / cancelled / refunded。"""
    __tablename__ = "orders"
    __table_args__ = (Index("ix_order_user_created", "user_id", "created_at"),)

    id = db.Column(Integer, primary_key=True)
    user_id = db.Column(Integer, ForeignKey("users.id", ondelete="CASCADE"),
                        nullable=False, index=True)
    product_id = db.Column(Integer, ForeignKey("products.id", ondelete="SET NULL"), nullable=True)
    order_no = db.Column(String(32), nullable=False, unique=True, index=True)
    plan = db.Column(String(16), nullable=False, default="premium")        # premium / pro
    amount_cents = db.Column(Integer, nullable=False, default=0, server_default="0")
    currency = db.Column(String(8), nullable=False, default="CNY")
    status = db.Column(String(16), nullable=False, default="pending", server_default="pending")
    created_at = db.Column(DateTime, nullable=False, default=utcnow, server_default=func.now())
    paid_at = db.Column(DateTime, nullable=True)


class Payment(db.Model):
    """支付流水（Mock 支付同样落库，保证可对账、可重放）。"""
    __tablename__ = "payments"
    __table_args__ = (Index("ix_payment_order", "order_id"),)

    id = db.Column(Integer, primary_key=True)
    order_id = db.Column(Integer, ForeignKey("orders.id", ondelete="CASCADE"),
                         nullable=False, index=True)
    provider = db.Column(String(16), nullable=False, default="mock")       # mock/alipay/wechat
    transaction_id = db.Column(String(64), nullable=True, index=True)
    amount_cents = db.Column(Integer, nullable=False, default=0, server_default="0")
    currency = db.Column(String(8), nullable=False, default="CNY")
    status = db.Column(String(16), nullable=False, default="pending", server_default="pending")
    raw = db.Column(Text, nullable=False, default="{}")                    # 网关回执 JSON
    created_at = db.Column(DateTime, nullable=False, default=utcnow, server_default=func.now())


# Onboarding 选项（与模板共用，单一来源）
AGE_GROUPS = [
    ("children", "儿童", "Children"),
    ("elementary", "小学", "Elementary School"),
    ("middle", "初中", "Middle School"),
    ("high", "高中", "High School"),
    ("university", "大学", "University"),
    ("adult", "成人", "Adult"),
    ("workplace", "职场", "Workplace"),
]
LEARNING_GOALS = [
    ("daily", "日常英语", "Daily English"),
    ("school", "学校英语", "School English"),
    ("exam", "考试英语", "Exam English"),
    ("workplace", "职场英语", "Workplace English"),
    ("business", "商务英语", "Business English"),
    ("travel", "旅行英语", "Travel English"),
    ("speaking", "口语", "Speaking"),
    ("listening", "听力", "Listening"),
]


# --------------------------------------------------------------------------
# V5.1 Master Lexicon（独立于 ContentItem 的主词库）
# --------------------------------------------------------------------------
class LexiconEntry(db.Model):
    """主词库条目（Master Lexicon）。

    与 ContentItem 解耦：词库只负责「词条本身」，不加 user_id、不绑定课程。
    课程由 scripts/lexicon_to_content.py 按 CEFR / 频率 / 主题 / 难度筛选后生成，
    绝不会把全部 7000 条自动灌进课程内容。

    去重键（数据库层强制唯一）：language_code + normalized + kind + pos
    —— 不按 surface 去重（同一词形可能有不同词性 / 不同 kind）。
    """

    __tablename__ = "lexicon_entries"
    __table_args__ = (
        Index("ix_lexicon_lang_norm", "language_code", "normalized"),
        Index("ix_lexicon_lang_cefr", "language_code", "cefr"),
        Index("ix_lexicon_lang_kind", "language_code", "kind"),
        Index("ix_lexicon_source", "source", "source_id"),
        UniqueConstraint(
            "language_code", "normalized", "kind", "pos",
            name="uq_lexicon_lang_norm_kind_pos",
        ),
    )

    id = db.Column(Integer, primary_key=True)

    # 语言：'en' / 'yue'
    language_code = db.Column(String(8), nullable=False, index=True)
    surface = db.Column(String(255), nullable=False)          # 原始词面 / 词组 / 句子
    lemma = db.Column(String(255), nullable=False, default="")  # 词目（原形）
    normalized = db.Column(String(255), nullable=False, default="")  # 归一化后用于去重/查询

    # 类型：vocabulary / phrase / sentence
    kind = db.Column(String(16), nullable=False, default="vocabulary")
    pos = db.Column(String(16), nullable=False, default="")     # 词性

    pronunciation = db.Column(String(128), nullable=False, default="")  # 英文 IPA
    jyutping = db.Column(String(128), nullable=False, default="")      # 粤拼（粤语必填）

    meaning_en = db.Column(Text, nullable=False, default="")     # 英文释义
    meaning_zh = db.Column(Text, nullable=False, default="")     # 中文释义
    example_en = db.Column(Text, nullable=False, default="")
    example_zh = db.Column(Text, nullable=False, default="")

    # 频率：wordfreq 提供的是频率分数 / 频率排名，绝不是 CEFR
    frequency = db.Column(db.Float, nullable=True)
    frequency_rank = db.Column(Integer, nullable=True)

    # CEFR：必须记录来源（cefr_source），不允许凭空声称
    cefr = db.Column(String(8), nullable=False, default="", index=True)   # Pre-A1/A1..C2
    cefr_source = db.Column(String(32), nullable=False, default="")

    # 难度：简单规则计算（cefr + 频率），不引入机器学习
    difficulty = db.Column(Integer, nullable=False, default=1, server_default="1")

    topic = db.Column(String(32), nullable=False, default="general")

    # 来源与许可证（以数据源当前公布的 License 为准）
    source = db.Column(String(32), nullable=False, default="")        # wordfreq/cc-canto/...
    source_id = db.Column(String(64), nullable=False, default="")
    license = db.Column(String(64), nullable=False, default="")
    license_url = db.Column(String(255), nullable=False, default="")
    attribution = db.Column(String(255), nullable=False, default="")

    # 商业授权：不确定时一律 False（绝不为填数据而假设可商用）
    commercial_allowed = db.Column(Boolean, nullable=False, default=False, server_default="0")
    redistribution_allowed = db.Column(Boolean, nullable=False, default=False, server_default="0")

    verified = db.Column(Boolean, nullable=False, default=False, server_default="0")

    # V5.2：是否达到「可进入生产内容」的标准。
    # 规则（见 V5.6 / §36）：verified + commercial_allowed + 必填字段有效 三者同时成立才为 True。
    # synthetic-dev 永远 False；legacy-2000 在许可证确认前为 False。
    production_ready = db.Column(Boolean, nullable=False, default=False, server_default="0",
                                index=True)

    created_at = db.Column(DateTime, nullable=False, default=utcnow, server_default=func.now())

    def __repr__(self) -> str:
        return f"<LexiconEntry {self.language_code}:{self.normalized!r} [{self.kind}/{self.pos}]>"
