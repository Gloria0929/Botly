# 群聊沙箱的预装镜像：ubuntu:24.04 + 常用命令行工具与 Python/Node
#
# 构建（只需一次，之后每个群聊的容器都从它启动）：
#   ./server/scripts/build_sandbox.sh
# 或手动：
#   docker build -f deploy/sandbox.Dockerfile -t botly-sandbox:24.04 deploy
#
# 想让容器用这个镜像，server/.env 里加：
#   BOTLY_LINUX_SANDBOX_IMAGE=botly-sandbox:24.04
#
# 注意：这是「给 Bot 用的电脑」，默认 root + 联网；不要在镜像里放任何密钥。

FROM ubuntu:24.04

ENV DEBIAN_FRONTEND=noninteractive \
    LANG=C.UTF-8 \
    PYTHONUNBUFFERED=1

RUN apt-get update && apt-get install -y --no-install-recommends \
        ca-certificates curl wget git openssh-client \
        unzip zip tar less file tree jq ripgrep vim-tiny \
        procps net-tools dnsutils \
        python3 python3-pip python3-venv \
        nodejs npm \
        build-essential pkg-config \
    && rm -rf /var/lib/apt/lists/*

# Ubuntu 24.04 的 pip 是 externally-managed，允许直接装包（容器里的便利优先于规范）
RUN python3 -m pip config set global.break-system-packages true || true

WORKDIR /workspace

# 容器由后端 `docker run -d ... sleep infinity` 拉起，这里的 CMD 只是兜底
CMD ["sleep", "infinity"]
