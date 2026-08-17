#!/usr/bin/env bash
# 读取 server/.env 中的端口/密码配置并启动 MySQL 容器
set -euo pipefail
cd "$(dirname "$0")"
set -a; source server/.env; set +a
docker compose up -d
echo "MySQL (ai-learn-mysql) 已启动，端口 ${MYSQL_PORT}。"
