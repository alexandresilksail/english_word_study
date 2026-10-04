#!/usr/bin/env bash
# ==========================================================================
# 单独申请 / 续签 Let's Encrypt 证书（deploy.sh 会自动调用，也可手动执行）
#   前置：域名 A 记录已指向本机、80 端口可访问、nginx 容器已用 http-only 配置启动
#   用法：
#     ./nginx/init-letsencrypt.sh
#     EMAIL=you@example.com ./nginx/init-letsencrypt.sh
# ==========================================================================
set -Eeuo pipefail
cd "$(dirname "$0")/.."

GREEN=$'\033[32m'; RED=$'\033[31m'; RESET=$'\033[0m'
info() { printf "%s[INFO]%s %s\n" "$GREEN" "$RESET" "$*"; }
err()  { printf "%s[ERROR]%s %s\n" "$RED" "$RESET" "$*"; }

if docker compose version >/dev/null 2>&1; then DC="docker compose"; else DC="docker-compose"; fi

# shellcheck disable=SC1091
set -a; . ./.env; set +a

if [ -z "${DOMAIN:-}" ] || [ "${DOMAIN:-}" = "www.example.com" ]; then
  err "请先在 .env 中设置 DOMAIN"; exit 1
fi
DOMAIN_MAIN=${DOMAIN%%,*}

EMAIL=${EMAIL:-}
if [ -z "$EMAIL" ]; then
  read -r -p "请输入邮箱（用于证书到期提醒）: " EMAIL
  grep -q '^EMAIL=' .env && sed -i "s|^EMAIL=.*|EMAIL=${EMAIL}|" .env || echo "EMAIL=${EMAIL}" >> .env
fi

DA=""
IFS=',' read -ra PARTS <<< "$DOMAIN"
for d in "${PARTS[@]}"; do DA="${DA} -d ${d}"; done

info "为 ${DOMAIN} 申请证书 ..."

# 用 http-only 配置启动 nginx
cp nginx/http-only.conf.template nginx/conf.d/app.conf
$DC up -d nginx
sleep 3

$DC run --rm --entrypoint "certbot certonly --webroot -w /var/www/certbot \
  --email ${EMAIL} --agree-tos --no-eff-email --keep-until-expiring ${DA}" certbot

# 切换为 HTTPS 配置
info "启用 HTTPS 配置并重载 Nginx"
sed "s|__DOMAIN__|${DOMAIN_MAIN}|g" nginx/conf.d/app.conf.template > nginx/conf.d/app.conf
$DC exec -T nginx nginx -t
$DC exec -T nginx nginx -s reload

info "完成！访问 https://${DOMAIN_MAIN}"
