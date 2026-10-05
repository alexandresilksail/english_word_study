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
