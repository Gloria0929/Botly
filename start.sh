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

echo "==> 检查依赖与模块结构"
( cd server && ./venv/bin/python -c "import app.main" ) || {
  echo "后端导入失败：先单独跑 'cd server && ./venv/bin/python -c \"import app.main\"' 看具体报错"
  exit 1
}

# 上一次没退干净的进程会占着端口/抢同一个队列，让新进程起不来又看不出原因
if [ "${SKIP_PREFLIGHT:-0}" != "1" ]; then
  for port in 8100 3200; do
    if lsof -nP -iTCP:"$port" -sTCP:LISTEN >/dev/null 2>&1; then
      echo "端口 $port 已被占用，可能是上次没退干净的实例；先执行：lsof -ti:$port | xargs kill"
      echo "（确认要复用现有进程可加 SKIP_PREFLIGHT=1 跳过检查）"
      exit 1
    fi
  done
  if command -v pgrep >/dev/null 2>&1 && pgrep -f "app\.worker" >/dev/null 2>&1; then
    echo "检测到已有 Worker 在跑，它还在用旧代码消费同一个队列；先执行：pkill -f 'app.worker'"
    exit 1
  fi
fi

echo "==> 检查 Redis"
( cd server && ./venv/bin/python -c "from app.core import redis_bus; print('redis ok' if redis_bus.available() else 'redis 不可用，将降级为 DB 轮询')" )

echo "==> 启动 API :8100"
# 用 exec 让子 shell 被 python 替换，$! 才是真正的进程号；
# 否则 trap 杀掉的是子 shell，uvicorn 会留下来继续占端口。
( cd server && exec ./venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8100 ) &
API=$!
echo "==> 启动 Worker"
( cd server && exec ./venv/bin/python -m app.worker ) &
WORKER=$!
echo "==> 启动 Web :3200"
( cd web && CODEBUDDY_SAFE_DELETE_ENABLED=0 exec npm run dev ) &
WEB=$!

trap 'kill $API $WORKER $WEB 2>/dev/null || true' INT TERM
wait
