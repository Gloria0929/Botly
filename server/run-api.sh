#!/usr/bin/env bash
# 启动 API（前台运行，Ctrl-C 退出）
set -euo pipefail
cd "$(dirname "$0")"

if [ ! -d venv ]; then
  echo "缺少虚拟环境，请先执行：python3 -m venv venv && ./venv/bin/pip install -r requirements.txt"
  exit 1
fi
if [ ! -f .env ]; then
  echo "缺少 .env，已从 .env.example 复制，请按需修改"
  cp .env.example .env
fi

exec ./venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port "${PORT:-8100}" "$@"
