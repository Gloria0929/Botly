#!/usr/bin/env bash
# 一键启动：API(8100) + Worker + Web(3200)
# 用法：./start.sh        前台，Ctrl-C 全部退出
set -euo pipefail
cd "$(dirname "$0")"

if [ ! -d server/venv ]; then
  echo "==> 初始化后端虚拟环境"
  ( cd server && python3 -m venv venv && ./venv/bin/pip install -q -r requirements.txt )
fi
if [ ! -f server/.env ]; then
  cp server/.env.example server/.env
  echo "==> 已生成 server/.env，如需改 Redis 密码请编辑它"
fi
if [ ! -d web/node_modules ]; then
  echo "==> 安装前端依赖"
  ( cd web && npm install --no-audit --no-fund )
fi

echo "==> 检查 Redis"
( cd server && ./venv/bin/python -c "from app import redis_bus; print('redis ok' if redis_bus.available() else 'redis 不可用，将降级为 DB 轮询')" )

echo "==> 启动 API :8100"
( cd server && ./venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8100 ) &
API=$!
echo "==> 启动 Worker"
( cd server && ./venv/bin/python -m app.worker ) &
WORKER=$!
echo "==> 启动 Web :3200"
( cd web && CODEBUDDY_SAFE_DELETE_ENABLED=0 npm run dev ) &
WEB=$!

trap 'kill $API $WORKER $WEB 2>/dev/null || true' INT TERM
wait
