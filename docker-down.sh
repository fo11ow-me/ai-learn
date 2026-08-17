#!/usr/bin/env bash
# 关闭前先导出 MySQL 数据（WHY：容器数据卷变更不自动持久化，见全局规范）
set -euo pipefail
cd "$(dirname "$0")"
set -a; source server/.env; set +a
mkdir -p server/backup
docker exec ai-learn-mysql sh -c 'exec mysqldump -u"$MYSQL_USER" -p"$MYSQL_PASSWORD" "$MYSQL_DATABASE"' > "server/backup/ai_learn_$(date +%Y%m%d_%H%M%S).sql"
docker compose down
echo "已导出备份并关闭 MySQL。"
