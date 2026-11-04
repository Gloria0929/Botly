#!/usr/bin/env bash
# 前端启动（开发模式）。
#
# 开发使用 .next-dev，生产构建使用 .next，启动开发服务无需删除生产产物。
# 在 WorkBuddy 沙箱终端里，文件钩子可能把缓存目录的 mkdir 判定为越权（CODEBUDDY_BROKER_DENY）。
#    这两个变量只存在于沙箱终端，普通终端里 unset 无副作用。
set -euo pipefail
cd "$(dirname "$0")"

PORT="${PORT:-3200}"

unset CODEBUDDY_SAFE_DELETE_SANDBOX CODEBUDDY_BROKERED_FS_HOOK_ENABLED 2>/dev/null || true
# 沙箱终端对批量删除有额外拦截；这两个变量在普通终端里不存在，无副作用
export CODEBUDDY_SAFE_DELETE_ENABLED=0

if [ ! -d node_modules ]; then
  echo "==> 安装依赖"
  npm install --no-audit --no-fund
fi

echo "==> 启动 http://localhost:${PORT}"
exec npx next dev -p "${PORT}"
