-- ============================================================================
-- V6.1 微信小程序：数据库迁移（UP）
--
-- 变更内容：新增 2 张表，**不 ALTER / 不 DROP 任何既有表**。
--   wechat_identities     微信身份 ↔ 站点账户关联
--   wx_token_revocations  JWT 撤销黑名单（登出 / 封号 / 令牌旋转）
--
-- 兼容性：SQLite / MySQL / PostgreSQL 通用写法（只用标准类型与约束）。
-- 回滚见同目录 001_wx_miniprogram_rollback.sql（DROP 两张新表，不动既有数据）。
--
-- 执行方式（任选其一）：
--   sqlite3 instance/english_word_study.db < migrations/001_wx_miniprogram.sql
--   psql "$DATABASE_URL" -f migrations/001_wx_miniprogram.sql
--   mysql ... < migrations/001_wx_miniprogram.sql
--
-- 幂等说明：本脚本**不是**幂等的（重复执行会报 "table already exists"）。
-- 这是有意的——迁移必须可审计，重复执行应当报错而不是静默跳过。
-- 已执行过的情况下请直接跳过；需要确认状态可执行 verify 查询（见文件末尾）。
-- ============================================================================

-- ----------------------------------------------------------------------------
-- 1. 微信身份关联表
-- ----------------------------------------------------------------------------
-- 唯一约束（防重复绑定的核心，与 models.WeChatIdentity.__table_args__ 一致）：
--   uq_wx_openid_appid : 同一 AppID 下，一个 openid 只能绑一个账户
--   uq_wx_user_appid   : 同一 AppID 下，一个账户只能绑一个微信身份
-- 外键 ON DELETE CASCADE：删除账户时自动清理关联行，不残留孤儿数据。
-- 说明：这里用内联 UNIQUE 而不是独立的 CREATE UNIQUE INDEX，
--       是为了与 SQLAlchemy create_all() 生成的 schema 完全一致，
--       避免「ORM 认为有约束、库里其实没有」的分歧。
CREATE TABLE IF NOT EXISTS wechat_identities (
    id            INTEGER     NOT NULL PRIMARY KEY,
    user_id       INTEGER     NOT NULL,
    openid        VARCHAR(64) NOT NULL,
    appid         VARCHAR(32) NOT NULL DEFAULT '',
    unionid       VARCHAR(64) NULL,
    nickname      VARCHAR(64) NOT NULL DEFAULT '',
    avatar_url    VARCHAR(512) NOT NULL DEFAULT '',
    created_at    DATETIME    NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at    DATETIME    NOT NULL DEFAULT CURRENT_TIMESTAMP,
    last_login_at DATETIME    NULL,
    CONSTRAINT uq_wx_openid_appid UNIQUE (openid, appid),
    CONSTRAINT uq_wx_user_appid   UNIQUE (user_id, appid),
    CONSTRAINT fk_wx_identity_user
        FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS ix_wx_identity_openid
    ON wechat_identities (openid);

CREATE INDEX IF NOT EXISTS ix_wx_identity_appid
    ON wechat_identities (appid);

CREATE INDEX IF NOT EXISTS ix_wx_identity_unionid
    ON wechat_identities (unionid);

-- ----------------------------------------------------------------------------
-- 2. JWT 撤销黑名单表
-- ----------------------------------------------------------------------------
-- jti 为主键（一条记录对应一枚被撤销的令牌）。
-- expires_at 用于定期清理：早于当前时间的行已自然失效，删除不影响安全性。
CREATE TABLE IF NOT EXISTS wx_token_revocations (
    jti         VARCHAR(64) NOT NULL PRIMARY KEY,
    user_id     INTEGER     NOT NULL,
    token_type  VARCHAR(16) NOT NULL DEFAULT 'access',
    revoked_at  DATETIME    NOT NULL DEFAULT CURRENT_TIMESTAMP,
    expires_at  DATETIME    NOT NULL,
    CONSTRAINT fk_wx_revocation_user
        FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS ix_wx_revocation_user
    ON wx_token_revocations (user_id);

-- ============================================================================
-- 验证查询（执行后应各返回一行，且列与本脚本一致）
-- ============================================================================
-- SQLite:
--   PRAGMA table_info(wechat_identities);
--   PRAGMA table_info(wx_token_revocations);
--   SELECT COUNT(*) FROM wechat_identities;      -- 新建库应为 0
--   SELECT COUNT(*) FROM wx_token_revocations;   -- 新建库应为 0
--
-- 确认既有数据完好（迁移不应影响任何既有行）：
--   SELECT COUNT(*) FROM users;
--   SELECT COUNT(*) FROM user_word_progress;
--   SELECT COUNT(*) FROM favorites;
