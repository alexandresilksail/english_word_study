-- ============================================================================
-- V6.1 微信小程序：数据库迁移（ROLLBACK）
--
-- 只 DROP 本次新增的 2 张表，**不触碰任何既有表与既有数据**。
-- 执行后微信小程序登录会失效（openid 关联丢失），Web 端完全不受影响。
--
-- 前置检查（强烈建议先做，确认不会误删）：
--   SELECT COUNT(*) FROM wechat_identities;     -- 有值说明已有人绑定过微信
--   SELECT COUNT(*) FROM wx_token_revocations;  -- 有值说明有未过期的撤销记录
--
-- 执行方式：
--   sqlite3 instance/english_word_study.db < migrations/001_wx_miniprogram_rollback.sql
--   psql "$DATABASE_URL" -f migrations/001_wx_miniprogram_rollback.sql
--
-- 注意：本脚本会**永久删除**微信绑定关系。已通过微信登录的用户在小程序端
-- 会被视为全新用户（自动建新号），其原账户的学习数据仍在 Web 端完好，
-- 但需要重新绑定才能找回。生产环境执行前请先备份数据库。
-- ============================================================================

-- SQLite / PostgreSQL：先删普通索引再删表；MySQL 会自动删除关联索引，
-- 下面的 DROP INDEX 在 MySQL 上需去掉 IF EXISTS（语法差异），
-- 因此 MySQL 建议直接执行两条 DROP TABLE。
-- 唯一约束是内联在表里的，DROP TABLE 会一并消失，无需单独处理。

DROP INDEX IF EXISTS ix_wx_identity_unionid;
DROP INDEX IF EXISTS ix_wx_identity_appid;
DROP INDEX IF EXISTS ix_wx_identity_openid;
DROP INDEX IF EXISTS ix_wx_revocation_user;

DROP TABLE IF EXISTS wx_token_revocations;
DROP TABLE IF EXISTS wechat_identities;

-- ============================================================================
-- 回滚后验证（两张表应不存在，既有表与数据完好）
-- ============================================================================
--   SELECT name FROM sqlite_master WHERE type='table'
--     AND name IN ('wechat_identities','wx_token_revocations');   -- 应返回 0 行
--   SELECT COUNT(*) FROM users;                 -- 与回滚前一致
--   SELECT COUNT(*) FROM user_word_progress;    -- 与回滚前一致
--   SELECT COUNT(*) FROM favorites;             -- 与回滚前一致
