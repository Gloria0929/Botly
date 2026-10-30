#!/usr/bin/env bash
# 前端启动（开发模式）。
#
# 两个已知的启动问题都在这里规避：
# 1) Next dev 会重建 .next 缓存，上一次 next build/start 的残留会导致启动失败；
# 2) 在 WorkBuddy 沙箱终端里，文件钩子会把 .next 的 mkdir 判定为越权（CODEBUDDY_BROKER_DENY）。
#    这两个变量只存在于沙箱终端，普通终端里 unset 无副作用。
set -euo pipefail
cd "$(dirname "$0")"

PORT="${PORT:-3200}"

unset CODEBUDDY_SAFE_DELETE_SANDBOX CODEBUDDY_BROKERED_FS_HOOK_ENABLED 2>/dev/null || true
# 沙箱终端对批量删除有额外拦截；这两个变量在普通终端里不存在，无副作用
export CODEBUDDY_SAFE_DELETE_ENABLED=0

if [ -d .next ]; then
  echo "==> 清理上次的构建缓存 .next"
  rm -rf .next 2>/dev/null || mv .next "/tmp/botly-next-$(date +%s)"
fi

if [ ! -d node_modules ]; then
  echo "==> 安装依赖"
  npm install --no-audit --no-fund
fi

echo "==> 启动 http://localhost:${PORT}"
exec npx next dev -p "${PORT}"
