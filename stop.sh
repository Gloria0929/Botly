#!/usr/bin/env bash
# 停掉本项目残留的 API / Worker / Web。
#
# 为什么需要它：`web/dev.sh` 用的是 `exec npx next dev`，npm 会再拉
# sh → node → next-server 一串子进程；如果它是被 IDE/预览按钮以独立会话
# （setsid）拉起来的，关终端、Ctrl-C 都碰不到它，表现就是「项目停了网页还能开」。
#
# 只结束「工作目录在本项目里」的监听进程，绝不按进程组杀，
# 避免误伤同机别的 3200/8100 服务、也不会把当前终端一起带走。
set -uo pipefail
cd "$(dirname "$0")"
ROOT="$(pwd -P)"
# 测试时可覆盖：BOTLY_STOP_PORTS=3999 ./stop.sh
PORTS="${BOTLY_STOP_PORTS:-8100 3200}"
killed=0

port_pids() {
  lsof -tiTCP:"$1" -sTCP:LISTEN 2>/dev/null || true
}

belongs() {
  local cwd
  cwd="$(readlink -f "/proc/$1/cwd" 2>/dev/null || true)"
  case "$cwd" in
    "$ROOT"|"$ROOT"/*) return 0 ;;
    *) return 1 ;;
  esac
}

stop_pid() {
  local pid="$1" cmd
  belongs "$pid" || return 0
  cmd="$(ps -o cmd= -p "$pid" 2>/dev/null | cut -c1-90)"
  echo "停止 PID $pid：$cmd"
  kill -TERM "$pid" 2>/dev/null || true
  killed=1
}

for port in $PORTS; do
  for pid in $(port_pids "$port"); do
    stop_pid "$pid"
  done
done

# Worker 不监听端口，按命令行找，再用工作目录过滤。
# 用 BOTLY_STOP_PORTS 指定端口时（测试/只停某个端口）跳过，避免误停 Worker。
if [ -z "${BOTLY_STOP_PORTS:-}" ]; then
  for pid in $(pgrep -f "app\.worker" 2>/dev/null || true); do
    [ "$pid" = "$$" ] && continue
    stop_pid "$pid"
  done
fi

if [ "$killed" = 1 ]; then
  sleep 1
  for port in $PORTS; do
    for pid in $(port_pids "$port"); do
      belongs "$pid" || continue
      echo "端口 $port 仍在监听，强制结束 PID $pid"
      kill -KILL "$pid" 2>/dev/null || true
    done
  done
  echo "已停止。"
else
  echo "没有发现本项目的残留进程。"
fi
