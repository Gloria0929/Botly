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
STOP_TIMEOUT="${BOTLY_STOP_TIMEOUT:-10}"
case "$STOP_TIMEOUT" in
  ''|*[!0-9]*) echo "BOTLY_STOP_TIMEOUT 必须是非负整数" >&2; exit 1 ;;
esac
for tool in lsof ps; do
  command -v "$tool" >/dev/null 2>&1 || { echo "缺少 $tool，无法检查进程归属" >&2; exit 1; }
done
targets=()

port_pids() {
  lsof -nP -tiTCP:"$1" -sTCP:LISTEN 2>/dev/null || true
}

belongs() {
  local cwd
  cwd="$(readlink -f "/proc/$1/cwd" 2>/dev/null || true)"
  # macOS 没有 /proc；lsof 的 cwd 字段同时支持 macOS 和 Linux。
  if [ -z "$cwd" ]; then
    cwd="$(lsof -a -p "$1" -d cwd -Fn 2>/dev/null | sed -n 's/^n//p')"
  fi
  case "$cwd" in
    "$ROOT"|"$ROOT"/*) return 0 ;;
    *) return 1 ;;
  esac
}

collect_pid() {
  local pid="$1" existing
  [ "$pid" = "$$" ] && return 0
  belongs "$pid" || return 0
  for existing in "${targets[@]:-}"; do
    [ "$existing" = "$pid" ] && return 0
  done
  targets+=("$pid")
}

alive() {
  local state
  state="$(ps -o stat= -p "$1" 2>/dev/null)"
  # 僵尸进程已经退出，只是在等待父进程回收。
  [ -n "$state" ] && [[ "$state" != *Z* ]]
}

for port in $PORTS; do
  for pid in $(port_pids "$port"); do
    collect_pid "$pid"
  done
done

# Worker 不监听端口，按命令行找，再用工作目录过滤。
# 用 BOTLY_STOP_PORTS 指定端口时（测试/只停某个端口）跳过，避免误停 Worker。
if [ -z "${BOTLY_STOP_PORTS:-}" ]; then
  for pid in $(pgrep -f "app\.worker" 2>/dev/null || true); do
    [ "$pid" = "$$" ] && continue
    collect_pid "$pid"
  done
fi

if [ "${#targets[@]}" = 0 ]; then
  echo "没有发现本项目的残留进程。"
  exit 0
fi

for pid in "${targets[@]}"; do
  belongs "$pid" || continue
  cmd="$(ps -o command= -p "$pid" 2>/dev/null | cut -c1-90)"
  echo "停止 PID ${pid}：$cmd"
  kill -TERM "$pid" 2>/dev/null || true
done

deadline=$((SECONDS + STOP_TIMEOUT))
while [ "$SECONDS" -lt "$deadline" ]; do
  remaining=0
  for pid in "${targets[@]}"; do
    if alive "$pid"; then remaining=1; break; fi
  done
  [ "$remaining" = 0 ] && break
  sleep 0.1
done

# Worker 不监听端口，也必须进入超时后的兜底清理。
for pid in "${targets[@]}"; do
  alive "$pid" && belongs "$pid" || continue
  echo "等待退出超时，强制结束 PID $pid"
  kill -KILL "$pid" 2>/dev/null || true
done
sleep 0.1

failed=0
for pid in "${targets[@]}"; do
  if alive "$pid" && belongs "$pid"; then
    echo "PID $pid 未能停止" >&2
    failed=1
  fi
done
for port in $PORTS; do
  for pid in $(port_pids "$port"); do
    if belongs "$pid"; then
      echo "端口 $port 仍被本项目 PID $pid 占用" >&2
      failed=1
    fi
  done
done
[ "$failed" = 0 ] || exit 1
echo "已停止。"
