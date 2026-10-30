#!/usr/bin/env bash
# 启动 Worker（前台运行）。Agent 循环全部在这里执行，不启动它任务不会动。
set -euo pipefail
cd "$(dirname "$0")"

if [ ! -d venv ]; then
  echo "缺少虚拟环境，请先执行：python3 -m venv venv && ./venv/bin/pip install -r requirements.txt"
  exit 1
fi
if [ ! -f .env ]; then
  cp .env.example .env
fi

exec ./venv/bin/python -m app.worker "$@"
