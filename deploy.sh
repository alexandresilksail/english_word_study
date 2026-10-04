#!/usr/bin/env bash
# ==========================================================================
# 一键部署脚本（在服务器上执行）
#   作用：构建镜像 → 启动容器 → 初始化数据库 → 申请 HTTPS 证书 → 切换 HTTPS
#   前置条件：服务器只装了 Docker 与 Docker Compose，不需要 Python
#
#   用法：
#     chmod +x deploy.sh
#     ./deploy.sh
# ==========================================================================
set -Eeuo pipefail

cd "$(dirname "$0")"

GREEN=$'\033[32m'; YELLOW=$'\033[33m'; RED=$'\033[31m'; RESET=$'\033[0m'
info() { printf "%s[INFO]%s %s\n" "$GREEN" "$RESET" "$*"; }
warn() { printf "%s[WARN]%s %s\n" "$YELLOW" "$RESET" "$*"; }
err()  { printf "%s[ERROR]%s %s\n" "$RED" "$RESET" "$*"; }

echo "===================================================="
echo "  英语单词学习 · 一键部署"
echo "===================================================="

# ---------------------------------------------------------------- 1. 环境检查
info "检查 Docker / Docker Compose ..."
command -v docker >/dev/null 2>&1 || { err "未检测到 Docker，请先安装： https://docs.docker.com/engine/install/ubuntu/"; exit 1; }
if docker compose version >/dev/null 2>&1; then
  DC="docker compose"
elif command -v docker-compose >/dev/null 2>&1; then
  DC="docker-compose"
else
  err "未检测到 Docker Compose，请先安装 docker-compose-plugin"; exit 1
fi
info "使用命令：$DC"

# ---------------------------------------------------------------- 2. 配置文件
if [ ! -f .env ]; then
  warn "未找到 .env，从模板创建 ..."
  cp .env.example .env
  if command -v openssl >/dev/null 2>&1; then
    KEY=$(openssl rand -hex 32)
    sed -i "s|^SECRET_KEY=.*|SECRET_KEY=${KEY}|" .env
    info "已自动生成随机 SECRET_KEY"
  fi
  err "请先编辑 .env，至少修改 DOMAIN / ADMIN_EMAIL / ADMIN_PASSWORD，然后重新运行本脚本"
  exit 1
fi

# shellcheck disable=SC1091
set -a; . ./.env; set +a

if [ -z "${DOMAIN:-}" ] || [ "${DOMAIN:-}" = "www.example.com" ]; then
  err "请在 .env 中设置你的真实域名 DOMAIN（例如 DOMAIN=words.yourdomain.com）"
  exit 1
fi
if [ -z "${SECRET_KEY:-}" ] || [ "${SECRET_KEY:-}" = "please-change-this-to-a-long-random-string" ]; then
  warn "SECRET_KEY 仍是占位值，正在生成随机密钥 ..."
  if command -v openssl >/dev/null 2>&1; then
    sed -i "s|^SECRET_KEY=.*|SECRET_KEY=$(openssl rand -hex 32)|" .env
    set -a; . ./.env; set +a
    info "已生成 SECRET_KEY"
  else
    err "请手动设置 SECRET_KEY"; exit 1
  fi
fi

DOMAIN_MAIN=${DOMAIN%%,*}
info "部署域名：$DOMAIN_MAIN"

# ---------------------------------------------------------------- 3. 目录与 Nginx 初始配置
mkdir -p data logs nginx/conf.d certbot/conf certbot/www

info "生成 Nginx 初始配置（仅 HTTP，用于申请证书）"
cp nginx/http-only.conf.template nginx/conf.d/app.conf

# ---------------------------------------------------------------- 4. 构建并启动
info "构建镜像并启动容器（首次约 2-5 分钟）..."
$DC up -d --build

info "等待 Web 服务就绪 ..."
for i in $(seq 1 40); do
  if $DC exec -T web python -c "import urllib.request;urllib.request.urlopen('http://127.0.0.1:8000/healthz',timeout=3)" >/dev/null 2>&1; then
    info "Web 服务已就绪"; break
  fi
  sleep 2
  if [ "$i" = "40" ]; then err "Web 服务启动超时，请查看日志：$DC logs web"; exit 1; fi
done

# ---------------------------------------------------------------- 5. 初始化数据
info "初始化数据库与词库 ..."
$DC exec -T web python -c "
from app import create_app
from extensions import db
from seeds.seed_words import seed_from_json
application = create_app()
with application.app_context():
    db.create_all()
    created, updated = seed_from_json(application.config['WORDS_JSON'])
    print(f'词库：新增 {created} / 更新 {updated}')
"

info "确保管理员账号存在 ..."
$DC exec -T -e ADMIN_EMAIL="${ADMIN_EMAIL:-}" -e ADMIN_PASSWORD="${ADMIN_PASSWORD:-}" \
  web python -c "
import os
from app import create_app
from extensions import db
from models import User
email = (os.environ.get('ADMIN_EMAIL') or '').strip().lower()
pwd = os.environ.get('ADMIN_PASSWORD') or ''
if not email or not pwd:
    print('未配置 ADMIN_EMAIL / ADMIN_PASSWORD，跳过创建管理员')
else:
    application = create_app()
    with application.app_context():
        u = User.query.filter_by(email=email).first()
        if u:
            u.is_admin = True
            print(f'已提升为管理员：{email}')
        else:
            u = User(email=email, username='管理员', is_admin=True)
            u.set_password(pwd)
            db.session.add(u)
            print(f'已创建管理员：{email}')
        db.session.commit()
"

# ---------------------------------------------------------------- 6. HTTPS
if [ "${SKIP_HTTPS:-}" = "1" ]; then
  warn "已设置 SKIP_HTTPS=1，跳过证书申请（将保持 HTTP）"
else
  if [ -f "certbot/conf/live/${DOMAIN_MAIN}/fullchain.pem" ]; then
    info "检测到已有证书，直接启用 HTTPS"
  else
    info "申请 Let's Encrypt 证书（请确保域名已解析到本机，且 80 端口可访问）"
    if [ -z "${EMAIL:-}" ]; then
      read -r -p "请输入用于证书通知的邮箱: " EMAIL_INPUT
      EMAIL=${EMAIL_INPUT:-admin@${DOMAIN_MAIN}}
      grep -q '^EMAIL=' .env && sed -i "s|^EMAIL=.*|EMAIL=${EMAIL}|" .env || echo "EMAIL=${EMAIL}" >> .env
    fi
    $DC run --rm --entrypoint "certbot certonly --webroot -w /var/www/certbot \
      --email ${EMAIL} --agree-tos --no-eff-email --keep-until-expiring \
      -d ${DOMAIN//,/ -d }" certbot || {
        err "证书申请失败。常见原因：域名未解析到本机 / 80 端口未放行 / 短时间内重复申请。"
        err "可先以 HTTP 方式访问 http://${DOMAIN_MAIN} 排障，之后再手动执行 init-letsencrypt.sh"
        exit 1
      }
  fi

  info "生成 HTTPS 配置并重载 Nginx"
  sed "s|__DOMAIN__|${DOMAIN_MAIN}|g" nginx/conf.d/app.conf.template > nginx/conf.d/app.conf
  $DC exec -T nginx nginx -t && $DC restart nginx
fi

# ---------------------------------------------------------------- 7. 完成
cat <<EOF

$GREEN================ 部署完成 ================$RESET
  访问地址：https://${DOMAIN_MAIN}
  管理员：${ADMIN_EMAIL:-（未配置）}
  数据库：$(pwd)/data/english_word_study.db
  日志目录：$(pwd)/logs
  查看状态：$DC ps
  查看日志：$DC logs -f web
  更新网站：./update.sh
  备份数据：./backup.sh
$GREEN==========================================$RESET
EOF
