#!/usr/bin/env bash
# ==========================================================================
# 更新网站（保留全部用户数据与已签发的证书）
#   1. 拉取最新代码（如果当前目录是 git 仓库）
#   2. 重新构建镜像
#   3. 重建 nginx 配置（防止模板更新）
#   4. 平滑重启容器（数据库在 ./data 卷中，不会丢失）
# ==========================================================================
set -Eeuo pipefail
cd "$(dirname "$0")"

GREEN=$'\033[32m'; YELLOW=$'\033[33m'; RESET=$'\033[0m'
info() { printf "%s[INFO]%s %s\n" "$GREEN" "$RESET" "$*"; }
warn() { printf "%s[WARN]%s %s\n" "$YELLOW" "$RESET" "$*"; }

if docker compose version >/dev/null 2>&1; then DC="docker compose"; else DC="docker-compose"; fi

# shellcheck disable=SC1091
set -a; . ./.env; set +a
DOMAIN_MAIN=${DOMAIN%%,*}

echo "================ 更新网站 ================"

# 1. 拉取代码
if [ -d .git ]; then
  info "拉取最新代码 ..."
  git pull || warn "git pull 失败（若是离线更新可忽略）"
fi

# 2. 备份数据库（更新前自动做一份快照）
if [ -f data/english_word_study.db ]; then
  mkdir -p backups
  TS=$(date +%Y%m%d_%H%M%S)
  cp data/english_word_study.db "backups/before_update_${TS}.db"
  info "已备份数据库 → backups/before_update_${TS}.db"
fi

# 3. 重建镜像并重启
info "重新构建镜像 ..."
$DC build web

info "同步词库到数据库 ..."
$DC run --rm web python -c "
from app import create_app
from extensions import db
from seeds.seed_words import seed_from_json
application = create_app()
with application.app_context():
    db.create_all()
    print(seed_from_json(application.config['WORDS_JSON']), '条词库记录已同步')
" || warn "词库同步未执行（旧容器可能仍在运行，属正常提示）"

info "平滑重启容器 ..."
# 先把服务放量到新版，再刷新 nginx 配置
$DC up -d --no-deps web
sleep 3
if [ -f nginx/conf.d/app.conf.template ] && [ -f "certbot/conf/live/${DOMAIN_MAIN}/fullchain.pem" ]; then
  sed "s|__DOMAIN__|${DOMAIN_MAIN}|g" nginx/conf.d/app.conf.template > nginx/conf.d/app.conf
  $DC exec -T nginx nginx -s reload || true
fi

# 4. 状态
info "容器状态："
$DC ps

cat <<EOF

$GREEN================ 更新完成 ================$RESET
  访问：https://${DOMAIN_MAIN}
  如页面异常： $DC logs -f web
  回滚办法：   还原代码后重新执行 ./update.sh
$GREEN==========================================$RESET
EOF
