# 微信小程序 API v1（`/api/wx/v1`）

> 面向「PolyGo」微信小程序的原生 JSON API，与 Web 端
> （`/api/v1`，Flask-Login Session 认证）**完全独立**：
> Web 继续用 Session Cookie，小程序用 **Bearer Token（标准 JWT）**。
>
> 实现：`wx_api.py`（蓝图）+ `wx_auth.py`（JWT 认证层）+ `wx_quiz_state.py`（出题状态令牌）
> 测试：`tests/test_wx_api.py`（72 项）+ `tests/test_jwt_logout_security.py`（JWT 登出/刷新安全 36 项）+ `tests/integration_miniprogram.py`（端到端 HTTP 联调 75 项）

## 1. 通用约定

| 项 | 值 |
|---|---|
| Base URL | `https://<你的域名>/api/wx/v1` |
| 请求体 | JSON（`Content-Type: application/json`） |
| 认证 | `Authorization: Bearer <access_token>` |
| 成功响应 | `{"ok": true, "data": {...}}` |
| 失败响应 | `{"ok": false, "error": {"code": "...", "message": "..."}}` |
| 分页响应 | `{"ok": true, "data": {"items": [...], "page": 1, "pages": 10, "total": 200}}` |

**两种令牌机制，不可互称：**

- **JWT（HS256，PyJWT）**：标识「你是谁」。Access 2 小时 / Refresh 30 天，
  claims 遵循 RFC 7519（`sub` 为字符串用户 ID、`typ`、`jti`、`iat`、`exp`）。
- **签名状态令牌（itsdangerous URLSafeSerializer）**：标识「这一轮测试进行到哪了」，
  仅用于测试中心四个接口，**不是 JWT**，2 小时过期。

### 错误码一览

| HTTP | code | 含义 |
|---|---|---|
| 400 | `bad_request` | 参数缺失 / 格式错误 |
| 400 | `no_words` | 出题范围内没有单词 |
| 400 | `invalid_state` / `missing_state` / `state_expired` / `out_of_range` | 测试状态令牌问题 |
| 401 | `missing_token` | 未带 Authorization 头 |
| 401 | `invalid_token` | 令牌伪造 / 签名错误 |
| 401 | `token_expired` | Access 过期（应调 refresh） |
| 401 | `token_revoked` | 已登出 / 已轮换 |
| 401 | `wx_error` / `wx_code_invalid` | 微信侧 code 无效 |
| 401 | `invalid_credentials` | 邮箱或密码不正确（不枚举邮箱） |
| 401 | `invalid_code` | 邮箱验证码错误 / 过期 |
| 404 | `user_not_found` | 令牌对应的账户已被删除 |
| 404 | `not_found` | 资源不存在（如单词 ID） |
| 409 | `already_bound` | 已绑定，需先解绑 |
| 429 | `rate_limited` | 登录 / 验证码频率超限 |
| 429 | `locked` | 账户临时锁定 |

### 数据隔离纪律

所有私有数据的 `user_id` **一律来自服务端验签后的 Access Token**（`g.wx_user.id`），
绝不读取客户端提交的 `user_id` / `uid`。用户 A 拿不到用户 B 的任何数据
（收藏 / 错题 / 进度 / 测试记录），测试第 [9] 节专项覆盖。

---

## 2. 认证接口

### 2.1 `POST /auth/wx-login` 微信一键登录

小程序端 `wx.login()` 拿到 code 后调用。服务端用 code 换 openid
（`code2Session`），`WX_AUTO_REGISTER=true` 时自动建号。

```jsonc
// 请求
{ "code": "wx.login 返回的一次性 code", "nickname": "可选", "avatar_url": "可选" }

// 响应 200
{
  "ok": true,
  "data": {
    "tokens": { "access_token": "...", "refresh_token": "...",
                "token_type": "Bearer", "expires_in": 7200 },
    "user": { "id": 1, "username": "...", "email": "", "email_bound": false,
              "is_admin": false, "level": 1, "xp": 0, "daily_goal": 20,
              "today_done": 0, "streak_days": 0, "preferred_lang": "",
              "created_at": "..." },
    "is_new_account": true,   // 首次登录自动建号
    "need_bind": false        // true = 服务端关闭自动注册，需走绑定流程
  }
}
```

- 响应**绝不包含** `session_key`（不返回、不落库、不打印）。
- `is_new_account=true` 时建议引导用户绑定邮箱（见 2.5）。
- 频率限制：同 IP 60 秒 30 次。

### 2.2 `POST /auth/refresh` 换发令牌（旋转）

```jsonc
// 请求（二选一）
{ "refresh_token": "..." }         // 推荐：body
// 或 Authorization: Bearer <refresh_token>

// 响应 200
{ "ok": true, "data": { "tokens": { "access_token": "...", "refresh_token": "..." } } }
```

**旋转语义**：旧 refresh 立即进黑名单，再次使用返回 401 `token_revoked`。
小程序请求层（`utils/api.js`）用单飞（single-flight）合并并发刷新，
避免多个页面同时刷新互相轮换。

### 2.3 `POST /auth/logout` 登出

```jsonc
// 请求（需要 Authorization 头；refresh_token 可选但建议带上）
{ "refresh_token": "..." }
// 响应 200
{ "ok": true, "data": { "revoked": ["access", "refresh"] } }
```

access 与 refresh 双双进黑名单立即失效。解绑（2.7）同样会撤销当前令牌。

### 2.4 `GET /auth/me` 当前用户

```jsonc
// 响应 200
{ "ok": true, "data": { "user": { /* 同 2.1 的 user */ } } }
```

`email` 对 `@wx.local` 占位账户返回 `""`，并用 `email_bound` 标识是否已绑真实邮箱。

### 2.5 `POST /auth/send-code` 发送绑定用邮箱验证码

```jsonc
// 请求
{ "email": "user@example.com" }
// 响应 200
{ "ok": true, "data": { "message": "验证码已发送", "dev_code": "123456" } }
```

`dev_code` **仅在服务端未配置 SMTP 时出现**（本地自测），生产环境绝不下发。
复用 `email_code` 的限流 / 一次性 / TTL 机制。

### 2.6 `POST /auth/bind-email` 绑定已有账户（需登录）

```jsonc
// 请求：两种证明方式任选其一
{ "email": "...", "password": "原账户密码" }
// 或
{ "email": "...", "code": "邮箱验证码" }

// 响应 200
{ "ok": true, "data": { "tokens": {...}, "user": {...}, "merged": true } }
```

安全规则：

1. **绝不仅凭邮箱自动关联**——必须验证对原账户的控制权（密码或邮箱验证码）。
2. 邮箱不存在时统一返回 `invalid_credentials`，**不枚举邮箱**。
3. 已绑定真实账户（非 `@wx.local` 占位）的微信**不允许直接改绑**，
   返回 409 `already_bound`，防止「抢绑」；合法换绑须先 2.7 解绑。
4. 若当前是占位账户且已产生学习数据，`merged=true` 表示数据已合并进目标账户
   （进度累加、收藏 / 错题去重、流水改挂、XP / 等级取高）。
5. 成功后**换发新令牌**（身份已切换），客户端必须保存。

### 2.7 `POST /auth/unbind` 解绑微信（需登录）

```jsonc
// 请求：真实账户必须再次验证；@wx.local 占位账户可不带凭证
{ "password": "原账户密码" }   // 或 { "code": "邮箱验证码" }

// 响应 200
{ "ok": true, "data": { "unbound": true } }
```

为什么需要再次验证：解绑后这枚 openid 就能绑到任意账户，不验证控制权
等于给「抢绑」开门。解绑**只删 `wechat_identities` 关联行**，
不删除账户、不删除任何学习数据，并立即撤销当前令牌。

---

## 3. 学习接口

### 3.1 `GET /home` 首页概览

```jsonc
// 响应 200（data 字段）
{
  "level": 3, "xp": 340, "xp_into_level": 60, "xp_for_next": 500,
  "xp_to_next": 160, "level_percent": 37, "title": "Explorer",
  "streak": 5, "best_streak": 7, "daily_goal": 20, "today_done": 12,
  "goal_percent": 60, "total_games": 2,
  "streak_days": 5,
  "stats": { "total_words": 2000, "learned": 120, "mastered": 45,
             "new_words": 1880, "favorites": 8, "wrong": 3,
             "total_answers": 400, "total_correct": 320,
             "today_answers": 15, "today_correct": 12,
             "today_accuracy": 80, "tests": 6, "views": 90,
             "progress_percent": 6, "accuracy": 80, "mastered_percent": 2 },
  "skills": [ { "skill": "vocabulary", "xp": 100, "items": 5, "minutes": 12 } ],
  "skills_meta": [ { "key": "vocabulary", "icon": "book", "color": "violet", "ready": true } ],
  "wrong_count": 3, "favorite_count": 8
}
```

### 3.2 `GET /words` 单词列表 / 搜索（可选登录）

| 参数 | 默认 | 说明 |
|---|---|---|
| `q` | 空 | 关键词（匹配单词或释义，大小写不敏感） |
| `page` | 1 | 页码 |
| `size` | 20 | 每页条数（上限 50） |
| `initial` | 空 | 首字母过滤（A-Z） |

带有效 Token 返回个性化字段（`status` / `fav` / `in_wrong` / 对错计数）；
匿名访问 `status` 统一为 `"new"`。**带了无效 Token 会 401**（不静默降级为匿名）。

```jsonc
// 响应 200
{ "ok": true, "data": { "items": [ {
    "id": 1, "word": "abandon", "phonetic_uk": "/əˈbændən/", "pos": "v.",
    "meaning_cn": "放弃；抛弃", "example_en": "...", "example_cn": "...",
    "level": "CET4", "status": "learning", "status_label": "学习中",
    "correct": 3, "wrong": 1, "view_count": 2, "fav": false,
    "in_wrong": true, "wrong_count": 1, "last_studied": "...",
    "audio_url": "/static/audio/abandon.mp3" } ],
  "page": 1, "pages": 100, "total": 2000 } }
```

### 3.3 `GET /words/<id>` 单词详情（可选登录）

响应字段同 3.2 单项（`build_word_view` 全量）。**登录后 `fav` 为真实收藏态**，
小程序「收藏」按钮据此渲染。每次访问记录一次 `view` 学习流水（与 Web 端一致）。

### 3.4 `POST /words/<id>/favorite` 收藏 / 取消收藏（需登录）

切换语义：已收藏则取消，未收藏则添加。返回最新状态：

```jsonc
{ "ok": true, "data": { "word_id": 1, "favorited": true } }
```

### 3.5 `GET /favorites` 收藏列表（需登录）

分页参数同 3.2；额外支持 `q` 搜索。每项含 `favorited_at`（收藏时间）。
按收藏时间倒序。

### 3.6 `GET /wrong` 错题本（需登录）

分页参数同 3.2；额外支持 `q` 搜索。每项含：

| 字段 | 说明 |
|---|---|
| `wrong_count` | 该词错误次数 |
| `last_answer` | 上次的错误答案 |
| `last_wrong_at` | 最近错误时间 |

### 3.7 `POST /wrong/<id>/remove` 移出错题本（需登录）

```jsonc
{ "ok": true, "data": { "word_id": 1, "removed": true } }
```

### 3.8 `GET /progress` 学习进度统计（需登录）

```jsonc
// 响应 200
{
  "stats": { /* 同 3.1 的 stats */ },
  "trend": [ { "day": "2026-10-01", "label": "10-01", "total": 20,
               "correct": 15, "percent": 75 } ],   // 固定 14 天
  "streak": 5,
  "gamification": { /* 同 3.1 的 level/xp 系列 */ },
  "badges": [ { "icon": "🏆", "name": "初露锋芒", "name_en": "First Steps",
                "name_alt": "...", "desc": "完成首次学习", "desc_en": "...",
                "unlocked": true } ],
  "skills": [ { "skill": "vocabulary", "xp": 100, "items": 5, "minutes": 12 } ]
}
```

### 3.9 `POST /me/goal` 设置每日目标（需登录）

```jsonc
// 请求：goal 为 1-500 的整数
{ "goal": 35 }
// 响应 200
{ "ok": true, "data": { "goal": 35 } }
```

---

## 4. 测试中心（无状态出题）

出题状态由客户端随请求带回，服务端验签还原。客户端必须把每次响应里的
`state` **覆盖本地旧值**再发下一个请求——服务端在 `state` 里写入了
「正确选项下标」与「计时起点」，丢了它判题就失去依据。

| 模式 | 题干 | 作答 |
|---|---|---|
| `choice` | 英文单词 + 音标 | 选中文释义（选项下标） |
| `zh_en` | 中文释义 | 选英文单词（选项下标） |
| `listen` | 音频（自动播放） | 选英文单词（选项下标） |
| `spell` | 中文释义 + 首字母 / 长度提示 | 输入英文拼写（文本） |

| 范围 | 含义 |
|---|---|
| `all` | 全部单词 |
| `new` | 未学（无进度记录） |
| `learning` | 学习中 |
| `mastered` | 已掌握 |
| `favorites` | 收藏 |
| `wrong` | 错题本 |

### 4.1 `POST /quiz/start` 开始测试

```jsonc
// 请求
{ "mode": "choice", "scope": "all", "size": 10 }
// mode ∈ TEST_MODES，scope ∈ TEST_SCOPES，size 1-50，均有默认值
// 响应 200
{ "ok": true, "data": { "state": "<签名状态令牌>", "mode": "choice", "total": 10 } }
```

范围内没有单词返回 400 `no_words`。

### 4.2 `GET /quiz/item?state=&i=` 取第 i 题（i 从 0 开始）

```jsonc
// 响应 200
{ "ok": true, "data": {
    "q": { "mode": "choice", "word_id": 1, "word": "abandon",
           "stem": "abandon", "stem_sub": "/əˈbændən/",
           "options": ["放弃；抛弃", "坚持；继续", ...],   // spell 模式无此项
           "first": "a", "length": 7,                      // 仅 spell 模式
           "audio_url": "/static/audio/abandon.mp3",       // 仅 listen 模式
           "i": 0, "total": 10 },
    "state": "<更新后的状态令牌>",   // 必须覆盖本地旧值
    "total": 10 } }
```

**`q` 绝不包含 `answer` 字段**——正确答案不下发客户端。

### 4.3 `POST /quiz/answer` 判题

```jsonc
// 请求
{ "state": "...", "i": 0, "answer": 2 }     // 选项下标；spell 模式为文本
// 响应 200
{ "ok": true, "data": {
    "correct": true,
    "expected": "放弃；抛弃",     // 本题正确答案文本
    "user_answer": "放弃；抛弃",  // 用户实际提交（选项文本或拼写）
    "word": "abandon", "phonetic": "əˈbændən",
    "meaning": "放弃；抛弃", "example_en": "...", "example_cn": "...",
    "audio": "abandon.mp3", "audio_url": "/static/audio/abandon.mp3",
    "mode": "choice",
    "state": "<更新后的状态令牌>",           // 必须覆盖本地旧值
    "progress": { "answered": 1, "total": 10 } } }
```

判题依据（正确选项下标）来自出题时写入 `state` 的值，**绝不重新生成随机选项**。
每次判题同时更新错题本与掌握度（`record_answer`）。

### 4.4 `POST /quiz/finish` 交卷

```jsonc
// 请求
{ "state": "..." }
// 响应 200
{ "ok": true, "data": {
    "total": 10, "score": 8, "percent": 80, "duration": 185,
    "mode": "choice",
    "comment": "good",        // outstanding ≥90 / good ≥70 / pass ≥50 / retry <50
    "record_id": 12 } }
```

成绩写入 `TestRecord`；`duration` 由 `state` 里的 `started_at`（首题签发时确定）
计算，重新签发不会重置。

---

## 5. 其它

| 接口 | 说明 |
|---|---|
| `GET /meta/skills` | 技能元数据（图标 / 配色 / 是否就绪），**无需登录** |
| `GET /health` | 健康检查，返回 `{"status": "ok", "api": "wx/v1"}` |

---

## 6. 客户端实现参考

小程序端的标准实现在 `miniprogram/utils/api.js`：

- 自动附加 `Authorization: Bearer`；
- 401 `token_expired` → 单飞刷新 → 原请求重放一次；
- 其它 401 → 清本地 Token 并 reLaunch 登录页；
- 网络错误 / 超时 / 5xx → 统一可读文案；
- 业务错误（`ok:false`）→ 抛出带 `code` 的异常，由页面自行处理
  （如 `no_words` 弹窗引导换范围、`invalid_state` 回设置页）。

测试矩阵见 `tests/test_wx_api.py` 的 docstring（16 节 72 项）与
`tests/integration_miniprogram.py`（真实 HTTP 服务全链路 75 项）。
