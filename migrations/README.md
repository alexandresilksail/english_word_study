# 数据库迁移与回滚说明（001_wx_miniprogram）

微信小程序功能引入 **2 张新表**，本迁移**不 ALTER、不 DROP、不重建任何既有表**。
既有网站、生产数据、用户账户、学习记录全部原样保留。

## 1. 变更内容

| 表 | 用途 | 关键约束 |
|---|---|---|
| `wechat_identities` | 微信身份（openid）↔ 站点账户关联 | `UNIQUE(openid, appid)` 一个微信只绑一个账户；`UNIQUE(user_id, appid)` 一个账户只绑一个微信；`FK user_id → users(id) ON DELETE CASCADE` |
| `wx_token_revocations` | JWT 撤销黑名单（登出 / 令牌旋转 / 封号） | `jti` 主键；`FK user_id → users(id) ON DELETE CASCADE`；`expires_at` 供定期清理 |

> 为什么用独立关联表而不是给 `users` 加 `openid` 列：一个账户未来可能绑多个
> AppID（小程序 + 公众号），且唯一约束需要数据库层强制，独立表更干净。

两张表的 schema 与 `models.py` 中 `WeChatIdentity` / `WxTokenRevocation`
的 SQLAlchemy `create_all()` 输出**逐列一致**（约束用内联 `UNIQUE` 而非独立
索引，避免「ORM 认为有约束、库里其实没有」的分歧）。

## 2. 执行（UP）

```bash
# 执行前：先备份！
cp instance/english_word_study.db instance/english_word_study.db.bak.$(date +%Y%m%d)

# SQLite
sqlite3 instance/english_word_study.db < migrations/001_wx_miniprogram.sql

# PostgreSQL
psql "$DATABASE_URL" -f migrations/001_wx_miniprogram.sql

# MySQL：先手动执行两条 CREATE TABLE（语法兼容），或直接依赖 ORM create_all()
```

**幂等性说明**：本脚本**有意不做成幂等**——重复执行会报
`table already exists`，这是特性不是缺陷：迁移必须可审计，
静默跳过会掩盖「到底执行没执行」的问题。已执行过就直接跳过。

## 3. 回滚（ROLLBACK）

```bash
cp instance/english_word_study.db instance/english_word_study.db.bak.$(date +%Y%m%d)
sqlite3 instance/english_word_study.db < migrations/001_wx_miniprogram_rollback.sql
```

回滚只 DROP 这 2 张新表，**不触碰任何既有表与数据**。
执行后：Web 端完全不受影响；小程序端微信关联丢失，已登录用户会被视为
新用户（自动建号），原账户学习数据仍在，重新绑定即可找回。
生产环境执行前必须备份（上面的 `cp` 就是干这个的）。

## 4. 验证（强烈建议按此流程执行）

不要直接在生产库上执行。`migrations/verify_migration.py` 会：

1. **复制生产库到临时文件**（绝不动原库）；
2. 记录基线（各表行数）；
3. 执行 UP，校验 schema / 唯一约束 / 既有数据零改动；
4. 行为验证：重复 openid 被拦截、同账户第二微信身份被拦截、
   不同 appid 下同一 openid 允许并存；
5. 执行 ROLLBACK，再校验一次；
6. 输出基线与迁移后各表行数对比。

```bash
python migrations/verify_migration.py
```

实测输出（2026-10-12，基于生产库副本）：

```
既有数据基线 → 迁移后 → 回滚后（全部一致，零改动）
users              11 → 11 → 11
words            2000 → 2000 → 2000
word_meta        2000 → 2000 → 2000
user_word_progress 20 → 20 → 20
study_records      20 → 20 → 20
user_stats         10 → 10 → 10
courses             9 → 9 → 9
units              28 → 28 → 28
lessons           261 → 261 → 261
```

## 5. 两条建表路径（选一，不要重复执行）

| 路径 | 适用场景 | 说明 |
|---|---|---|
| **ORM 自动**（`db.create_all()`，`app.py` 启动时执行） | 默认行为，开发 / 自运维部署 | 后端一发版，两张新表自动就绪，无需任何操作 |
| **SQL 迁移**（本目录 `001_wx_miniprogram.sql`） | 生产库由 DBA 管控、需要可审计 DDL 的场景 | 手工执行，schema 与 ORM 输出逐列一致 |

两条路径产出**相同 schema**，选其一即可。若 ORM 已自动建过表，
再执行 SQL 迁移会报 `table already exists`——这正是有意的保护
（迁移不可静默重复执行，见第 2 节），此时直接跳过即可。

时序建议（自运维部署，默认走 ORM 路径）：

1. 直接发后端代码，`create_all()` 自动建表，Web 端零影响；
2. 需要留审计痕迹时，改用 SQL 迁移路径并在变更单登记；
3. 放量小程序登录前跑一遍 `tests/run_all.py` 与
   `tests/integration_miniprogram.py`。
