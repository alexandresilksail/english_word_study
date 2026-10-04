# 英语单词学习 · Web SaaS 版

一个可以真正放到公网访问的英语单词学习网站：**多用户注册登录、每人独立学习数据、2000 词 A-Z 词库、离线英式真人发音、四种测试模式、收藏/错题本/学习进度、简易后台**。

服务器只需要装 **Docker + Docker Compose**，不需要装 Python / Nginx / Gunicorn——这些全部打包在镜像里。

---

## 一、功能一览

### 词库
- **2000 个常用英语单词**，A–Z 全部 26 个首字母均有覆盖
- 每个单词包含：英文单词、**英式 IPA 音标**、词性、中文释义、英文例句、中文例句、考试标签、词频
- 已校验：**无重复词、无空白字段、无 TODO/测试占位词**（见 `tests/test_flow.py` 第 1 节）
- 发音为**离线 MP3**（edge-tts 英式女声 en-GB），共 2000 个文件 18MB，不依赖任何在线 TTS 服务

### 用户与数据隔离
- 注册 / 登录 / 退出，密码用 **Werkzeug PBKDF2 哈希**存储（永不存明文）
- 每个用户的**学习记录、收藏、错题本、测试记录、单词进度完全隔离**，SQL 层按 `user_id` 过滤

### 学习
- 单词学习（卡片式：单词 + 音标 + 释义 + 例句 + 发音）
- 今日学习、连续打卡、学习/已掌握状态标记
- A–Z 浏览、关键词搜索、单词详情

### 测试（4 种模式 × 6 种范围）
| 模式 | 说明 |
|---|---|
| `choice` 英文 → 中文 | 四选一 |
| `zh_en` 中文 → 英文 | 四选一 |
| `listen` 听音测试 | 播放音频选单词 |
| `spell` 拼写测试 | 听音/看释义拼写单词 |

范围：全部单词 / 未学习 / 学习中 / 已掌握 / 我的收藏 / 错题本，题量 10/20/30/50。

### 其它
- 收藏、错题本（自动收录 + 手动移除）
- 学习进度（总量、已掌握、正确率、趋势）
- 简易后台 `/admin`：用户数、用户列表、词库数、学习次数、测试次数、今日活跃
- 响应式界面，桌面 / 手机都能用

---

## 二、技术栈

| 层 | 选型 |
|---|---|
| Web 框架 | Flask 3.1（应用工厂 `create_app`） |
| ORM | SQLAlchemy 2.1 + Flask-SQLAlchemy |
| 认证 | Flask-Login（Session + 记住我） |
| 表单/安全 | Flask-WTF + WTForms（全局 CSRF） |
| 数据库 | SQLite（默认），可平滑切 MySQL / PostgreSQL |
| WSGI | Gunicorn（2 worker × 2 线程，适配 2C2G） |
| 反向代理 | Nginx 1.27-alpine（80→443，HTTPS，安全头，静态资源缓存） |
| 证书 | Let's Encrypt / Certbot（自动签发 + 自动续签） |
| 容器 | Docker + Docker Compose |
| 前端 | 原生 HTML / CSS / JS（无构建步骤） |

---

## 三、目录结构

```
saas/
├── app.py                       # Flask 应用工厂、错误处理、CLI 命令
├── wsgi.py                      # WSGI 入口
├── config.py                    # 全部配置（环境变量驱动，不写死域名/密钥）
├── extensions.py                # db / login_manager
├── models.py                    # 7 张数据表 + 模型方法
├── forms.py                     # WTForms 表单（注册/登录/资料/密码/空表单）
├── auth.py                      # 注册 / 登录 / 退出（含失败锁定）
├── services.py                  # 业务层：出题、判分、统计、数据隔离查询
├── utils/ratelimit.py           # 登录失败锁定
├── routes/
│   ├── main.py                  # / /dashboard /profile /healthz
│   ├── words.py                 # /learn /words /word/<id> /favorites /wrong /progress + 三个 action API
│   ├── quiz.py                  # /test + /api/quiz/* + /api/audio/<id>
│   └── admin.py                 # /admin /admin/users /admin/api/stats
├── seeds/seed_words.py          # 词库导入（幂等，可重复执行）
├── app/
│   ├── templates/               # base + 18 个页面 + 4 个错误页
│   ├── static/css/saas.css      # 样式
│   ├── static/js/saas.js        # 交互逻辑
│   ├── static/img/              # SVG 插画与图标
│   ├── static/audio/            # 2000 个离线英式发音 MP3
│   └── data/words.json          # 2000 词原始数据
├── nginx/
│   ├── conf.d/app.conf.template     # HTTPS 配置模板（含 __DOMAIN__ 占位符）
│   ├── http-only.conf.template      # 仅 HTTP（签发证书阶段使用）
│   └── init-letsencrypt.sh          # 单独申请/续签证书
├── tests/test_flow.py           # 93 项端到端测试
├── Dockerfile                   # 两阶段构建，非 root 用户运行
├── docker-compose.yml           # web + nginx + certbot 三容器
├── gunicorn.conf.py             # 生产 Gunicorn 配置
├── deploy.sh                    # 一键部署
├── update.sh                    # 一键更新（保留数据与证书）
├── backup.sh                    # 数据库备份（可挂 crontab）
├── requirements.txt
├── .env.example
└── README.md
```

---

## 四、本地运行

> 需要 Python 3.11+（推荐 3.12）。**Windows / macOS / Linux 均可**。

```bash
cd saas

# 1. 创建虚拟环境
python -m venv .venv
# Windows:
.venv\Scripts\activate
# macOS / Linux:
source .venv/bin/activate

# 2. 安装依赖
pip install -r requirements.txt

# 3. 准备配置文件
cp .env.example .env
# 编辑 .env：FLASK_ENV=development，SECRET_KEY 随便填一串长随机串
python -c "import secrets;print(secrets.token_hex(32))"

# 4. 初始化数据库 + 导入 2000 词 + 创建管理员
export FLASK_APP=app.py        # Windows: set FLASK_APP=app.py
flask bootstrap                 # 一条命令完成：建表 + 导词 + 建管理员

# 5. 启动
python app.py
# 浏览器打开 http://127.0.0.1:5000
```

也可以分步执行：

```bash
flask init-db                   # 只建表
flask seed-words                # 只导词库（幂等，重复执行安全）
ADMIN_EMAIL=me@demo.com ADMIN_PASSWORD=Study123456 flask create-admin
```

> 数据库文件位置：`instance/english_word_study.db`

---

## 五、本地测试

测试用 Flask 测试客户端跑，**不需要启动服务器、不需要 Docker**：

```bash
python tests/test_flow.py
```

输出（节选）：

```
==============================================================
结果：通过 93 / 失败 0
==============================================================
```

覆盖内容：

| # | 小节 | 校验点 |
|---|---|---|
| 1 | 词库完整性 | 2000 条、无重复、无空释义/空音标/空例句、A–Z 覆盖 |
| 2 | 注册 | 成功注册、重复邮箱拒绝、弱密码拒绝 |
| 3 | 登录 | 成功登录、错误密码拒绝 |
| 4 | 登录保护 | 未登录访问 dashboard / learn / favorites / admin 均跳转登录 |
| 5 | 发音 | MP3 文件存在且可读取 |
| 6 | 学习 | 学习页、标记状态、收藏、我再想想 |
| 7 | 收藏/错题 | 收藏列表、取消收藏、错题写入与移除 |
| 8 | 四种测试 | choice / zh_en / listen / spell 全链路出题–答题–判分 |
| 9 | 数据隔离 | A 用户看不到 B 用户的收藏/错题/进度/记录 |
| 10 | 后台 | 非管理员 403，管理员可见用户数/词库数/学习次数/测试次数 |
| 11 | 进度 | 今日学习、累计学习、正确率统计 |
| 12 | 退出/重登 | 退出后受保护页不可访问，可重新登录 |
| 13 | 登录锁定 | 连续失败触发锁定，解锁后可登录 |
| 14 | 个人资料 | 改用户名、改密码、旧密码失效 |
| + | CSRF | 缺 token 的 POST 返回 400 |

---

## 六、环境变量（`.env`）

复制 `.env.example` 为 `.env` 后修改。**`.env` 不要提交到 Git。**

| 变量 | 必填 | 说明 |
|---|---|---|
| `FLASK_ENV` | ✅ | `development` / `production`（生产必须为 production） |
| `SECRET_KEY` | ✅ | Session 与 CSRF 签名密钥，`python -c "import secrets;print(secrets.token_hex(32))"` 生成 |
| `DOMAIN` | ✅ | 你的域名，**不带 `http://`**，多个用逗号分隔 |
| `APP_TITLE_CN` | | 站点标题，默认「英语单词学习」 |
| `DATABASE_URL` | | 留空则用容器内 SQLite；迁移数据库时填 MySQL/PG 连接串 |
| `ADMIN_EMAIL` | | 首次部署自动创建的管理员邮箱 |
| `ADMIN_PASSWORD` | | 管理员初始密码（上线后请立即在个人中心修改） |
| `EMAIL` | | 证书到期通知邮箱（`deploy.sh` 会提示写入） |
| `SESSION_COOKIE_SECURE` | | HTTPS 下建议 `true` |
| `SKIP_HTTPS` | | 设为 `1` 时 `deploy.sh` 跳过证书申请，保持 HTTP（内网测试用） |

---

## 七、数据模型（7 张表）

| 表名 | 作用 | 关键字段 |
|---|---|---|
| `users` | 用户 | email(唯一)、username、password_hash、is_admin、login 失败计数/锁定时间、created_at、last_seen |
| `words` | 词库 | word(唯一)、phonetic_uk、pos、meaning_cn、example_en、example_cn、level tags、freq |
| `user_word_progress` | 每用户每词进度 | (user_id, word_id) 唯一、status(new/learning/mastered)、study_count、wrong_count、last_studied_at |
| `favorites` | 收藏 | (user_id, word_id) 唯一、created_at |
| `wrong_answers` | 错题本 | user_id、word_id、mode、user_answer、correct、created_at |
| `test_records` | 测试记录 | user_id、mode、scope、total、correct、accuracy、duration、created_at |
| `study_records` | 学习记录 | user_id、word_id、action、created_at |

**迁移 MySQL / PostgreSQL 无需改任何模型代码**（不依赖 SQLite 专有字段），只需改 `.env`：

```bash
DATABASE_URL=mysql+pymysql://user:password@host:3306/dbname?charset=utf8mb4
# 或
DATABASE_URL=postgresql+psycopg2://user:password@host:5432/dbname
```

并把对应驱动加进 `requirements.txt`（`pymysql` / `psycopg2-binary`）。

---

## 八、路由清单

| 路径 | 方法 | 说明 | 登录 |
|---|---|---|---|
| `/` | GET | 首页（未登录为落地页，已登录转 dashboard） | — |
| `/register` | GET/POST | 注册 | — |
| `/login` | GET/POST | 登录 | — |
| `/logout` | GET/POST | 退出 | ✅ |
| `/dashboard` | GET | 个人主页：今日学习、进度概览 | ✅ |
| `/learn` | GET | 单词学习（卡片 + 发音 + 标记状态） | ✅ |
| `/words` | GET | A–Z 浏览 + 搜索 | ✅ |
| `/word/<id>` | GET | 单词详情 | ✅ |
| `/favorites` | GET | 我的收藏 | ✅ |
| `/wrong` | GET | 错题本 | ✅ |
| `/progress` | GET | 学习进度 | ✅ |
| `/profile` | GET/POST | 个人资料 / 修改密码 | ✅ |
| `/test` | GET | 测试中心 | ✅ |
| `/api/quiz/start` | POST | 开始测试（mode + scope + size） | ✅ |
| `/api/quiz/item` | GET | 取当前题目 | ✅ |
| `/api/quiz/answer` | POST | 提交答案并判分 | ✅ |
| `/api/quiz/finish` | POST | 结束并写入 test_records | ✅ |
| `/api/audio/<word_id>` | GET | 取该单词的离线 MP3 | ✅ |
| `/action/toggle-favorite` | POST | 收藏/取消收藏 | ✅ |
| `/action/remove-wrong` | POST | 移出错题 | ✅ |
| `/action/set-status` | POST | 设置单词掌握状态 | ✅ |
| `/admin` | GET | 后台概览 | 管理员 |
| `/admin/users` | GET | 用户列表 | 管理员 |
| `/admin/api/stats` | GET | 统计 JSON | 管理员 |
| `/healthz` | GET | 健康检查（容器 healthcheck 用） | — |

---

## 九、部署到阿里云（完整 17 步）

> 目标：一台服务器、一个域名、一条命令上线，**HTTPS 自动签发**。

### 第 1 步：购买云服务器

阿里云 ECS → 创建实例，推荐配置：

| 项 | 推荐值 |
|---|---|
| 地域 | 离用户最近的地域（中国大陆地域需 ICP 备案，见第 9 步说明） |
| 实例规格 | **2 vCPU / 2 GB 内存**（如 ecs.e-c1m1.large 或共享型 2C2G），最低 1C1G 也能跑 |
| 操作系统 | **Ubuntu 24.04 LTS 64位** |
| 存储 | 40GB ESSD Entry（2000 词 + 18MB 音频占用极小） |
| 带宽 | 按使用流量 3–5 Mbps，或固定带宽 1–2 Mbps |

### 第 2 步：设置 root 密码

控制台 → 实例详情 → **重置实例密码**，设置一个强密码，立即重启生效。

### 第 3 步：开放端口（安全组）

控制台 → 该实例 → **安全组** → 配置规则 → 入方向 → 手动添加：

| 协议 | 端口范围 | 授权对象 | 说明 |
|---|---|---|---|
| TCP | 22 | 你的 IP（或 0.0.0.0/0） | SSH |
| TCP | 80 | 0.0.0.0/0 | HTTP（证书签发必须） |
| TCP | 443 | 0.0.0.0/0 | HTTPS |

> ⚠️ **80 端口必须放行**，Let's Encrypt 的 HTTP-01 校验要用它。

### 第 4 步：SSH 连接服务器

```bash
ssh root@你的公网IP
```

Windows 可用 PowerShell 自带的 `ssh`，或用 PuTTY / Xshell。

### 第 5 步：更新系统

```bash
apt update && apt upgrade -y
apt install -y curl git unzip
```

### 第 6 步：安装 Docker（含 Compose 插件）

```bash
curl -fsSL https://get.docker.com | sh
systemctl enable --now docker
docker version
docker compose version
```

国内服务器若拉取慢，可配置镜像加速：

```bash
mkdir -p /etc/docker
cat > /etc/docker/daemon.json <<'EOF'
{
  "registry-mirrors": ["https://docker.m.daocloud.io", "https://hub-mirror.c.163.com"]
}
EOF
systemctl restart docker
```

### 第 7 步：上传代码到服务器

**方式 A：在本机用 scp 上传**（推荐，无需 Git）

```bash
# 在本机 PowerShell / 终端执行（不要 ssh 登录上去）
# 先在本地把 saas 目录打成 zip
scp saas.zip root@你的公网IP:/root/
ssh root@你的公网IP "cd /opt && unzip -o /root/saas.zip && mv saas EnglishWordStudy"
```

**方式 B：放 Git 仓库后在服务器 clone**

```bash
cd /opt && git clone https://github.com/你的账号/english-word-study.git EnglishWordStudy
```

下文统一假设代码路径为 `/opt/EnglishWordStudy`。

### 第 8 步：配置 `.env`

```bash
cd /opt/EnglishWordStudy
cp .env.example .env
nano .env
```

必须修改的三项：

```bash
FLASK_ENV=production
SECRET_KEY=这里填一长串随机字符        # 生成：openssl rand -hex 32
DOMAIN=words.yourdomain.com           # 你的真实域名，不带 http://
ADMIN_EMAIL=you@example.com
ADMIN_PASSWORD=改成强密码至少8位
```

> `SECRET_KEY` 一键生成：`openssl rand -hex 32`

### 第 9 步：域名解析（A 记录）

到你的域名服务商 DNS 控制台，添加一条 **A 记录**：

| 主机记录 | 记录类型 | 记录值 |
|---|---|---|
| `words`（或 `@`） | A | 服务器公网 IP |

等待生效后在本机验证：

```bash
ping words.yourdomain.com
```

> ⚠️ **国内服务器（阿里云中国大陆地域）需要完成 ICP 备案**，否则 80/443 端口会被拦截，证书也无法签发。未备案时可：① 用中国香港/新加坡等地域；② 先用 IP 以 HTTP 方式（`SKIP_HTTPS=1`）自测。

### 第 10 步：一键部署

```bash
cd /opt/EnglishWordStudy
chmod +x deploy.sh update.sh backup.sh nginx/init-letsencrypt.sh
./deploy.sh
```

脚本会依次完成：检查 Docker → 校验 `.env` → 生成 Nginx HTTP 配置 → **构建镜像并启动三容器**（web / nginx / certbot）→ 等待健康检查通过 → **建表 + 导入 2000 词** → 创建管理员 → **申请 Let's Encrypt 证书** → 切换 HTTPS 配置并重载 Nginx。

首次构建约 3–5 分钟。结束时输出：

```
================ 部署完成 ================
  访问地址：https://words.yourdomain.com
  管理员：you@example.com
  数据库：/opt/EnglishWordStudy/data/english_word_study.db
```

> 若暂时不想申请证书（比如域名还没解析好），用：`SKIP_HTTPS=1 ./deploy.sh`，先以 HTTP 跑起来，之后随时执行 `./nginx/init-letsencrypt.sh` 补签。

### 第 11 步：Nginx 反向代理说明（已自动配置，可跳过手动）

`deploy.sh` 会自动把 `nginx/conf.d/app.conf.template` 里的 `__DOMAIN__` 替换成你的域名，生成 `nginx/conf.d/app.conf`。链路：

```
浏览器 ──HTTPS(443)──▶ Nginx ──HTTP(8000)──▶ Gunicorn ──▶ Flask ──▶ SQLite
                        └─ /static/ 静态资源直接返回 + 30 天缓存
```

Nginx 已内置：HTTP→HTTPS 301 跳转、TLS1.2/1.3、HSTS、`nosniff`、`X-Frame-Options`、CSP、Referrer-Policy、`server_tokens off`、gzip 压缩、8MB 上传限制。

### 第 12 步：HTTPS 证书（已自动申请，手动补签方式如下）

`deploy.sh` 已用 webroot 方式签发证书。若签发失败，排查完 DNS/端口后手动执行：

```bash
./nginx/init-letsencrypt.sh
```

证书存放在 `certbot/conf/live/<你的域名>/`，由 `certbot` 容器**每 12 小时自动检查续签**，无需人工干预。

### 第 13 步：启动站点并验证

```bash
docker compose ps            # 三个容器都应是 Up
docker compose logs -f web   # 看应用日志
curl -I https://words.yourdomain.com
```

浏览器打开 `https://words.yourdomain.com`，逐项验证：

- [ ] 注册新账号 → 自动登录进入 dashboard
- [ ] 单词学习页能听到发音
- [ ] 收藏 / 取消收藏正常
- [ ] 四种测试模式均能出题、答题、判分
- [ ] 错题本自动收录错误答案
- [ ] 学习进度数字正确
- [ ] **F5 刷新页面数据仍在**
- [ ] 退出后用另一个账号登录，**看不到上一个账号的任何数据**
- [ ] 用 `ADMIN_EMAIL` 登录，访问 `/admin` 能看到统计数据
- [ ] `docker compose restart` 之后，账号、进度、收藏、错题**全部还在**（数据卷持久化）
- [ ] 极端验证：`docker compose down` 再 `docker compose up -d`，数据依然完整

```bash
# 持久化验证
docker compose exec -T web python -c "
from app import create_app
from extensions import db
from models import User, Word, Favorite
a = create_app()
with a.app_context():
    print('用户数', User.query.count(), '词库数', Word.query.count(), '收藏数', Favorite.query.count())
"
```

### 第 14 步：日常更新

```bash
cd /opt/EnglishWordStudy
./update.sh
```

脚本会：拉最新代码（若为 Git 仓库）→ **自动备份数据库快照** → 重新构建镜像 → 同步词库 → 平滑重启容器 → 刷新 Nginx 配置。**用户数据保留**（在 `./data` 卷中），**证书保留**（在 `./certbot` 卷中）。

回滚：把代码还原到上一版，再执行一次 `./update.sh` 即可。

### 第 15 步：备份数据

手动备份：

```bash
./backup.sh
# 输出：backups/ews_20261004_153000.db.gz
```

加入定时任务（每天凌晨 3 点）：

```bash
crontab -e
# 追加这一行：
0 3 * * * cd /opt/EnglishWordStudy && ./backup.sh >> logs/backup.log 2>&1
```

备份自动保留最近 14 天。恢复方法：停止容器后，把 `.db.gz` 解压覆盖回 `data/english_word_study.db` 再启动。

```bash
docker compose down
gunzip -c backups/ews_20261004_153000.db.gz > data/english_word_study.db
docker compose up -d
```

### 第 16 步：查看日志

```bash
docker compose logs -f            # 全部容器
docker compose logs -f web        # 应用日志（Gunicorn access + error）
docker compose logs -f nginx      # Nginx 访问/错误日志
docker compose logs --tail=200 web
tail -f logs/app.log              # Flask 应用层异常日志
docker stats                      # CPU / 内存占用
```

### 第 17 步：重启 / 停止 / 开机自启

```bash
docker compose restart            # 全部重启
docker compose restart web        # 只重启应用
docker compose down               # 停止并移除容器（数据在 ./data，不会丢）
docker compose up -d              # 启动
docker compose down && docker compose up -d --build   # 清理重建
```

`docker-compose.yml` 中三个容器都设置了 `restart: always` / `unless-stopped`，**服务器重启后会自动拉起**，无需手动操作。

---

## 十、管理员后台

- 用 `.env` 中 `ADMIN_EMAIL` 注册的账号登录后访问 `https://你的域名/admin`
- 超级管理员也可命令行追加：

```bash
ADMIN_EMAIL=me@demo.com ADMIN_PASSWORD=Study123456 docker compose exec -T web flask create-admin
```

- 后台展示：注册用户数、今日活跃用户、词库单词数、累计学习次数、累计答题次数、累计测试次数、用户明细列表
- 普通用户访问 `/admin` 返回 403

---

## 十一、安全清单（均已实现）

| 项 | 实现 |
|---|---|
| 密码存储 | Werkzeug `generate_password_hash`（PBKDF2-SHA256），**不存明文、不可逆** |
| 会话 | Flask-Login + 签名 Cookie；`HttpOnly` + `SameSite=Lax`；HTTPS 下自动 `Secure` |
| CSRF | Flask-WTF 全局开启，所有 POST 表单与 AJAX 携带 token，缺失返回 400 |
| SQL 注入 | 全部走 SQLAlchemy ORM 参数化查询，无字符串拼 SQL |
| XSS | Jinja2 默认自动转义；CSP 响应头限制脚本来源 |
| 暴力破解 | 连续 5 次登录失败锁定 15 分钟（`MAX_LOGIN_ATTEMPTS` / `LOGIN_LOCK_MINUTES` 可调） |
| 越权访问 | 所有业务查询按 `current_user.id` 过滤；`/admin` 二次校验 `is_admin` |
| 调试模式 | 生产 `FLASK_ENV=production`，`DEBUG=False`，异常不回显堆栈（返回 500 页面） |
| 传输安全 | 强制 HTTPS + HSTS，HTTP 301 跳转 |
| 容器安全 | 镜像内以非 root 用户 `appuser` 运行，两阶段构建减小体积 |
| 请求限制 | `MAX_CONTENT_LENGTH=8MB`，Nginx `client_max_body_size 8m` |
| 敏感配置 | 域名 / 密钥全部走环境变量 `.env`，代码中零硬编码 |

---

## 十二、常见问题排查

| 现象 | 原因与处理 |
|---|---|
| `deploy.sh` 报证书申请失败 | ① 域名 A 记录未生效（`ping 域名` 看是否为服务器 IP）；② 80 端口安全组未放行；③ 国内服务器未备案；④ 同一域名短时间多次签发被限流（等 1 小时）。可先 `SKIP_HTTPS=1 ./deploy.sh` 用 HTTP 跑通，再手动补签 |
| 访问 502 Bad Gateway | web 容器还没起来或崩了：`docker compose logs web`；等 20 秒再刷 |
| 页面没有样式/音频 404 | 镜像未包含静态目录，执行 `docker compose build --no-cache web && docker compose up -d` 重建 |
| 数据「 database is locked 」 | SQLite 已配置 30s 超时与 `check_same_thread=False`；若并发进一步增大，建议切 PostgreSQL |
| 数据更新了但页面还是旧的 | 浏览器缓存静态资源，Ctrl+F5 强刷 |
| 忘记管理员密码 | `ADMIN_EMAIL=x@y.com ADMIN_PASSWORD=NewPass123 docker compose exec -T web flask create-admin`（已存在则提升为管理员并重置密码） |
| 服务起不来，端口被占 | `sudo lsof -i :80` / `:443` 查占用进程，停掉后重启 compose |
| 想换端口 | 改 `docker-compose.yml` 里 nginx 的 `ports` 映射（容器内 80/443 保持不变） |

---

## 十三、交付清单

| 文件 | 作用 |
|---|---|
| `Dockerfile` | 两阶段构建 Python 3.12 + 依赖 + 应用 + 2000 音频 |
| `docker-compose.yml` | web / nginx / certbot 三容器编排 + 数据卷挂载 |
| `gunicorn.conf.py` | 生产 WSGI 配置（2C2G 友好） |
| `nginx/conf.d/app.conf.template` | HTTPS 反向代理模板（含安全头、静态缓存） |
| `nginx/http-only.conf.template` | 证书签发阶段的临时 HTTP 配置 |
| `nginx/init-letsencrypt.sh` | 独立申请/续签证书脚本 |
| `deploy.sh` | 一键部署：构建 → 启动 → 初始化 → 建管理员 → 签发证书 → 切 HTTPS |
| `update.sh` | 一键更新：备份 → 重建 → 同步词库 → 平滑重启 |
| `backup.sh` | SQLite 安全备份 + 14 天轮转 |
| `seeds/seed_words.py` | 数据库初始化脚本（幂等） |
| `tests/test_flow.py` | 93 项端到端测试 |
| `.env.example` | 环境变量模板 |
| `README.md` | 本文件 |

---

## 十四、许可证

内部项目，按实际需求调整使用。词库的释义与例句来自公开词典数据，发音音频由 TTS 合成，仅供学习用途。
