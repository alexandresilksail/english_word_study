#!/usr/bin/env bash
# ==========================================================================
# SQLite 数据库备份（建议加入 crontab 每天执行）
#   用法： ./backup.sh
#   定时任务： 0 3 * * * cd /opt/EnglishWordStudy && ./backup.sh >> logs/backup.log 2>&1
# ==========================================================================
set -Eeuo pipefail
cd "$(dirname "$0")"

mkdir -p backups

SRC="data/english_word_study.db"
if [ ! -f "$SRC" ]; then
  echo "[ERROR] 未找到数据库文件：$SRC"
  exit 1
fi

TS=$(date +%Y%m%d_%H%M%S)
OUT="backups/ews_${TS}.db"

# SQLite 官方推荐的安全备份方式
if command -v sqlite3 >/dev/null 2>&1; then
  sqlite3 "$SRC" ".backup '$OUT'"
else
  docker run --rm -v "$(pwd)/data:/data" -v "$(pwd)/backups:/backups" alpine:3.20 \
    sh -c "cp /data/english_word_study.db /backups/ews_${TS}.db" || cp "$SRC" "$OUT"
fi

gzip -f "$OUT"
echo "[OK] 备份完成：${OUT}.gz  ($(du -h "${OUT}.gz" | cut -f1))"

# 保留最近 14 天的备份
find backups -name "ews_*.db.gz" -type f -mtime +14 -delete 2>/dev/null || true
find backups -name "before_update_*.db" -type f -mtime +14 -delete 2>/dev/null || true
echo "[OK] 已清理 14 天前的旧备份，当前备份数：$(find backups -type f | wc -l)"
