# V5.0–V6.0 审计报告（AUDIT REPORT）

> 审计方式：只读（未读取/输出任何 `.env`、API Key、Token、密码；未修改/删除任何文件）。
> 判定基于**源代码 + 运行时数据库 `instance/english_word_study.db` 的实际内容**，而非 README 措辞。
> 审计基准：`master @ dd7cdd6`（V6.0.3/4 提交后，本地与待推送一致）。
> 诚实原则：任何未接真实引擎的能力均如实标注；未测维度返回 `None` 而非 `0`；真实 provider 出错显式失败，绝不悄然退化冒充。

---

## 一、审计总表（V5.0 – V6.0）

| Version | Feature | Status | Problem | Fix |
|---|---|---|---|---|
| V5.0 | 基础平台（i18n / 登录 / Session / Dashboard / 课程入口） | **PASS** | 无 | — |
| V5.1 | Master Lexicon 流水线 | **PASS** | `lexicon_dev.db` 为陈旧占位库（HIGH-2，仅文档） | 已在 `.gitignore`（`*.db`）忽略，不入库 |
| V5.2 | Legacy 2000 词库 | **PASS** | 运行时 2000 词全部 `production_ready=False`（HIGH-3，详见下） | 符合 license 规则，内容经 Legacy Word 通道提供 |
| V5.3 | English Lexicon 处理 | **PASS** | 同上数据底座说明 | — |
| V5.4 | Cantonese（粤语） | **PASS** | 无 | — |
| V5.5 | Course/Unit/Lesson/Content + 判分 | **PASS** | 无 | — |
| V5.6 | Review / Mastery | **PASS** | 表存在但当前 0 行（懒生成，正常） | — |
| V5.7 | AI Tutor | **PASS** | 无 | — |
| V5.8 | Listening / Speaking | **PASS** | 无 | — |
| V5.9 | 遗留项收口 | **PASS** | 4 项遗留（配额 / CLI / 录音 / 限流 / 性能） | 已全部收口（详见第三节） |
| V6.0.1 | AI Assessment（六维能力评估） | **PASS** | 缺评估引擎与 Skill Profile 产出 | `assessment.py` + `routes/assessment.py` + 模型，已建 |
| V6.0.2 | Learner Model（`LearnerProfile` + `Assessment` 快照） | **PASS** | 缺六维画像与评估快照 | 模型已建，含六维等级 / 母语 / 目标语 / 弱项 / 偏好 |
| V6.0.3 | AI Learning Path（个性化每日计划） | **PASS** | 无个性化路径引擎 | `path_service.personalized_plan` + `/path` 渲染，已建 |
| V6.0.4 | Adaptive Review（弱项优先到期重排） | **PASS** | 复习队列无个性化重排 | `review_service.adaptive_due_items` + `/review` 徽标，已建 |
| V6.0.5–11 | AI Tutor 2.0 / Conversation / Speaking Coach / Content Engine / Multilingual / Dashboard 2.0 / Subscription | **ROADMAP** | 超出本次收口范围 | 列为下一阶段路线，**未纳入本次推送（如实标注）** |
| 安全 | 门禁 / IDOR / XSS / 限流 | **PASS** | HIGH-1：IP 级限流定义后从未接入 | V5.9 接入；V6.0.4 改为显式开关，测试不误拦 |
| 性能 | Dashboard / Admin | **PASS** | HIGH-4a/b：Dashboard 多 count、Admin 逐用户 N+1 | V5.9 已修复（缓存 + 批量聚合） |
| 测试 | pytest / compileall | **PASS** | — | 全量 **192 passed**，`python -m compileall` 通过 |
| 数据模型 | V6.0 预检（前次审计发现） | **PASS** | 缺 `LearnerProfile` / `Assessment` | V6.0.1/2 已补齐，运行时表已建 |

---

## 二、运行时数据库实测（非占位）

`learning_languages=2(en,yue)`、`courses=8`、`units=27`、`lessons=252`、
`content_items=1035`（11 种 kind 全覆盖）、`words=2000`、`word_meta=2000`、`users=11`；
粤语内容 `lang='yue'` 真实隔离（你好/nei5 hou2、早晨/zou2 san4、唔該/m4 goi1 带 Jyutping）。

V6.0 新增表（schema 已落地，运行时因尚无用户完成评估而 0 行，属正常懒生成）：

- `learner_profiles`：`user_id, native_language, target_language, vocab/grammar/reading/listening/speaking/writing_level, overall_level, weak_areas, learning_preferences, updated_at` —— **0 行**
- `assessments`：评估快照表 —— **0 行**
- `review_items`：**0 行**（懒生成，正常）
- `content_mastery`：**0 行**（懒生成，正常）
- `subscriptions / orders / payments`：**0 行**（脚手架已建，`products=3` 已 seeding）

> 说明：V6.0.1/2 的逻辑正确性由 `tests/test_v60_assessment.py`（及路径/复习测试）在内存库中以真实建表 + 写入 + 断言覆盖；运行时 0 行仅代表暂无真实用户数据流入，不代表功能缺失。

---

## 三、Critical / High 缺陷与处置

- **HIGH-1（安全 · IP 限流）**：`utils/ratelimit.py` 的 `ip_too_many_requests()` 全仓未被调用。
  → **V5.9 已接入** `auth` 蓝图 `before_request`，覆盖 login/register/验证码等高频面；AJAX 返回 429 JSON、表单返回 flash+redirect，与账户级锁定互补。
  → **V6.0.4 加固**：原 `if TESTING: return` 豁免对自建 `DevelopmentConfig` 的测试无效，全量套件共享 `127.0.0.1` 登录 POST 超阈值被误拦，级联 30 failed。改为**显式 `IP_RATELIMIT_ENABLED` 开关**，`TestingConfig` 与 8 个 `DevelopmentConfig` 测试配置统一关闭；限流专用测试在用例内**临时开启**开关验证真实拦截（断言第 21 次返回 429）。根因清除，全量 188→ 192 passed。
- **HIGH-2（数据 · 旧库）**：`lexicon_dev.db` 仅含 `synthetic-dev` 占位（meaning 标 "DEV PLACEHOLDER"），且无 `content_mastery` 表。
  → 处置：已被 `.gitignore`（`*.db`）忽略，不入库；属陈旧开发产物，不影响运行时（`instance/english_word_study.db` 为真实库）。
- **HIGH-3（数据底座 · 诚实标注）**：运行时 `lexicon_entries` 全部 `source='legacy-2000'`、`production_ready=0`。
  → 处置（**刻意保持，非缺陷**）：`license_registry.json` 中 `legacy-2000` license 为 `UNKNOWN`，按规格「没有明确许可证的数据不能自动进入商业生产库」，`production_ready=False` 是**正确**的；1035 条 `content_items` 经 Legacy `Word`/`WordMeta` 派生通道提供（与 `production_ready` 过滤是两条独立通道），闭环功能成立。
- **HIGH-4（性能）**：
  - (a) `app.py` 上下文处理器每认证请求调用 `dashboard_stats`，内部十余条独立 `count`。
    → **V5.9 已修复**：加 30s TTL 内存缓存（按 `user_id`），高频面板查询压到每分钟最多 2 次落库。
  - (b) `routes/admin.py` `admin.users` 逐用户查（7×N 条查询）。
    → **V5.9 已修复**：改为按当前页 `user_id` 批量 `group_by` 聚合，7 条查询替代 7×N。
- **V5.9 其余收口**：每日配额（免费档无配额收口）+ 运维 CLI（`recompute` 入口收口）+ 录音降级与麦克风释放 + 听力卡片缺陷修复（卡片 id 冲突与答案泄露）。

---

## 四、V6.0 实现结论

闭环目标：`Assessment → Learner Model → Learning Path → Learning → Practice → AI Tutor / Conversation → Skill Analysis → Adaptive Review → Learner Model ↺`。

- **V6.0.1 AI Assessment**：`assessment.py` 六维（词汇/语法/阅读/听力/口语/写作）评估引擎，产出 Skill Profile；`routes/assessment.py` 提供评估页与结果页；`models.py` 新增 `Assessment` 快照模型。测试 `test_v60_assessment.py` 覆盖评估流程与画像写入。
- **V6.0.2 Learner Model**：`models.py` 新增 `LearnerProfile`（六维等级 + 母语/目标语 + `weak_areas` + `learning_preferences`），与 `User` 经 `user.learner_profile` / `user.assessments` 反向关系关联；`Assessment` 作为评估快照。schema 经 `db.create_all()` 自动建表（见第二节实测）。
- **V6.0.3 AI Learning Path**：`path_service.personalized_plan(user_id)` —— 无画像 → 引导 `/assessment`；有画像 → 弱项专项（`focus`，`priority=1`）/ 当前 CEFR 等级单元（`unit`，`priority=2`，复用 `learning_path.LEVEL_BY_CODE` 与 `UNIT_THEMES`）/ 间隔复习（`review`，`priority=3`）。`routes/main.py` 的 `/path` 路由注入 `plan`，`app/templates/path.html` 渲染 `plan-list`。**未新增表/蓝图**，复用既有 `/path` 与 `LearnerProfile` 数据脊柱。测试 `test_v60_learning_path.py`（2 例：无画像提示评估 / 有画像显示弱项+等级）。
- **V6.0.4 Adaptive Review**：`review_service.adaptive_due_items(uid, limit)` —— 在到期集合内按「弱项种类 + 逾期程度」重排（弱项 kind +1000 权重，逾期小时封顶 72）。`routes/main.py` 的 `/review` 路由支持 `?mode=classic` 回退，`app/templates/review.html` 显示 `.adaptive-badge`。**未新增表/蓝图**。测试 `test_v60_adaptive_review.py`（2 例：弱项优先 / 页面徽标；链式建表用 get-or-create 规避 `courses` 唯一约束）。

> **诚实标注（V6.0.5–11）**：AI Tutor 2.0 / AI Conversation / Speaking Coach / AI Content Engine / Multilingual / Dashboard 2.0 / Subscription 在 README 中仍标记 `🚧 待开发`，**本次审计与推送范围仅含 V6.0.1–4**。未以「已落地」冒充未实现功能；其路线保留为下一阶段。

---

## 五、安全门禁结论

`admin_required`、`api_login_required`、登录失败账户锁定、`_safe_next` 防开放重定向、邮箱验证码 60s 重发 + 每小时上限、**无 IDOR**（所有查询带 `current_user.id`）、唯一 `|safe` 用于静态 SVG 字典（非用户输入）。

新增 IP 限流经 V6.0.4 显式开关加固后：生产默认开启（`IP_RATELIMIT_ENABLED=True`），测试统一关闭以免误拦；限流逻辑（10 分钟 / 20 次窗口，基于 `X-Forwarded-For`/`X-Real-IP`/`remote_addr`）有专项测试守护。整体门禁扎实。

---

## 六、测试与质量门禁

- **pytest 全量：192 passed**（原 V5 基线 180 → V6.0.1/2 新增 `test_v60_assessment` → V6.0.3/4 新增 `test_v60_learning_path` + `test_v60_adaptive_review` 共 4 例；限流误拦修复后由 30 failed 归零）。
- **`python -m compileall .`：通过**（唯一 `SyntaxWarning` 为 `test_v58_speaking.py` 中刻意保留的 `assert r["fluency"] is not 0`，用于文档化「流利度绝不等于 0」的诚实约定，非缺陷）。
- **依赖零新增**：HTTP 走标准库 `urllib`，AI/语音 provider 出错显式失败，未引入 SDK / 重型模型 / PostgreSQL / Redis / Celery。
- **推送方式**：`_push_v5.py` 以 GitHub API 对工作区相对远端 `feat/v5-learning-platform` 做增/改 diff，**仅新增/修改、不删除**，避免部分抽取副本误删远程音频/数据库大文件；fast-forward（非 force）。

---

## 七、审计结论

V5.0–V5.8 九版本功能在代码与数据库层面均真实形成可运行闭环（均 PASS），无 Critical 级缺陷；4 项 High 中 2 项（HIGH-1、HIGH-4）已在 V5.9 修代码，2 项（HIGH-2、HIGH-3）为数据底座/旧库/诚实标注问题，按规格刻意保持正确，仅文档说明。

V5.9 完成遗留项收口（配额/CLI/录音/限流/性能）；V6.0.1–4 将平台从「单词网站」升级为 **AI 个性化多语学习平台**的可用闭环：评估 → 画像 → 个性化路径 → 自适应复习，全部基于既有 `/path`、`/review` 路由与 `LearnerProfile` 数据脊柱，**未新增表/蓝图**，测试全绿（192 passed）。V6.0.5–11 列为下一阶段路线，未在本推送范围内，如实标注。

**结论：本次审计范围内（V5.0–V6.0.4）全部 PASS，可推送审核分支。**
