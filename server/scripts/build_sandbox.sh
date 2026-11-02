#!/usr/bin/env bash
# 构建群聊沙箱的预装镜像（python3 / node / git / jq … 都装好）。
#
# 用法：
#   ./scripts/build_sandbox.sh          # 已存在就跳过
#   FORCE=1 ./scripts/build_sandbox.sh  # 强制重建
#   ./scripts/build_sandbox.sh my-tag   # 自定义镜像名
#
# 构建好之后，server/.env 里指定 BOTLY_LINUX_SANDBOX_IMAGE=botly-sandbox:24.04
# 新群聊的容器就会秒起，不用再等 apt。
set -euo pipefail

SERVER_DIR="$(cd "$(dirname "$0")/.." && pwd)"
ROOT_DIR="$(cd "$SERVER_DIR/.." && pwd)"
IMAGE="${1:-botly-sandbox:24.04}"
DOCKERFILE="$ROOT_DIR/deploy/sandbox.Dockerfile"

if ! command -v docker >/dev/null 2>&1; then
  echo "未找到 docker，请先安装 Docker" >&2
  exit 1
fi

if [ "${FORCE:-0}" != "1" ] && docker image inspect "$IMAGE" >/dev/null 2>&1; then
  echo "镜像 $IMAGE 已存在，跳过构建（重建请加 FORCE=1）"
  exit 0
fi

echo "==> 构建 ${IMAGE}（首次约 2–4 分钟，需要联网）"
docker build -f "$DOCKERFILE" -t "$IMAGE" "$ROOT_DIR/deploy"

echo
echo "构建完成：$IMAGE"
echo "在 server/.env 里加一行即可启用："
echo "  BOTLY_LINUX_SANDBOX_IMAGE=$IMAGE"
