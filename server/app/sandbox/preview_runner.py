"""在沙箱内运行的 stdlib 服务管理器；绝不在 Botly 宿主机执行项目代码。"""
import base64
import fcntl
import hashlib
import http.client
import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import time

MAX_BODY = 8 * 1024 * 1024
STARTUP_TIMEOUT = 300
START_WAIT = 1


def listening(port):
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=.3):
            return True
    except OSError:
        return False


def identity(pid):
    try:
        # 启动时刻避免 PID 重用后误停其他服务。
        fields = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()
        return fields[19] if fields[0] != "Z" else None
    except (OSError, IndexError):
        return None


def alive(state):
    return bool(state and state.get("identity") and identity(state["pid"]) == state["identity"])


def run(payload):
    config, action = payload["config"], payload["action"]
    directory = Path(config["state_dir"])
    directory.mkdir(parents=True, exist_ok=True)
    state_file = directory / "service.json"
    signature = hashlib.sha256(json.dumps(config, sort_keys=True).encode()).hexdigest()
    port = config["port"]
    with (directory / "lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            state = json.loads(state_file.read_text())
        except (OSError, ValueError):
            state = {}

        def status():
            running = alive(state)
            ready = running and state.get("signature") == signature and listening(port)
            started_at = state.get("started_at") or (state_file.stat().st_mtime if state else time.time())
            elapsed = max(0, int(time.time() - started_at))
            log = directory / "backend.log"
            tail = ""
            if log.is_file():
                with log.open("rb") as stream:
                    stream.seek(max(0, log.stat().st_size - 4000))
                    tail = stream.read().decode("utf-8", errors="replace")
            if ready:
                phase, message = "ready", f"后端已就绪，端口 {port} 已开始监听。"
            elif not running:
                phase = "failed" if state else "stopped"
                message = "启动进程已退出，请检查启动命令和日志。" if state else "后端未运行。"
            elif state.get("signature") != signature:
                phase, message = "config_changed", "启动配置已变化，请先停止旧后端再启动。"
            elif elapsed >= STARTUP_TIMEOUT:
                phase = "timeout"
                message = f"启动进程仍在运行，但 {port} 端口等待 {elapsed} 秒后仍未监听。请检查依赖安装、启动命令和监听端口。"
            else:
                phase, message = "starting", f"启动进程正在运行，等待 {port} 端口开始监听。首次安装依赖可能需要较长时间。"
            if running and not ready and not tail:
                message += "进程暂未输出日志。"
            return {"running": running, "ready": ready, "port": port, "log": tail,
                    "phase": phase, "message": message, "elapsed_seconds": elapsed,
                    "timeout_seconds": STARTUP_TIMEOUT, "log_path": str(log)}

        if action == "status":
            return status()
        if action == "stop":
            if alive(state):
                os.killpg(state["pid"], signal.SIGTERM)
                deadline = time.monotonic() + 3
                while alive(state) and time.monotonic() < deadline:
                    time.sleep(.1)
                if alive(state):
                    os.killpg(state["pid"], signal.SIGKILL)
            state_file.unlink(missing_ok=True)
            state = {}
            return status()
        if action == "start":
            if alive(state):
                if state.get("signature") != signature:
                    raise ValueError("启动配置已变化，请先停止旧后端再启动")
            else:
                if listening(port):
                    raise ValueError(f"端口 {port} 已被占用，请使用空闲端口或停止原服务")
                env = {**os.environ, "PORT": str(port), "HOST": "0.0.0.0", "PYTHONUNBUFFERED": "1"}
                # 子进程独立会话、重定向全部标准流；工具调用结束不会终止后端。
                with (directory / "backend.log").open("ab") as log:
                    log.write((f"\n[Botly preview] 启动后端，等待端口 {port}\n"
                               f"工作目录：{config['cwd']}\n启动命令：{config['command']}\n").encode())
                    log.flush()
                    # 继承沙箱已经准备好的环境；避免重复执行 login profile 导致换目录或阻塞。
                    proc = subprocess.Popen(["bash", "-c", config["command"]], cwd=config["cwd"],
                        env=env, stdin=subprocess.DEVNULL, stdout=log, stderr=log, start_new_session=True)
                state = {"pid": proc.pid, "identity": identity(proc.pid), "signature": signature,
                         "started_at": time.time()}
                state_file.write_text(json.dumps(state))
            # 长启动留给前端 status 轮询，不占住单次请求及生命周期锁。
            deadline = time.monotonic() + START_WAIT
            while alive(state) and not listening(port) and time.monotonic() < deadline:
                time.sleep(.2)
            return status()
        if action != "request":
            raise ValueError("未知预览操作")
        if not status()["ready"]:
            raise ValueError("后端未启动或已退出，请在预览面板启动后端")

    # HTTP 请求不持有生命周期锁，多个 API 可以并发执行。
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=25)
    try:
        conn.request(payload["method"], payload["path"], body=base64.b64decode(payload.get("body", "")),
                     headers=payload.get("headers", {}))
        response = conn.getresponse()
        body = response.read(MAX_BODY + 1)
        if len(body) > MAX_BODY:
            raise ValueError("后端响应超过 8 MB 限制")
        return {"status": response.status, "headers": dict(response.getheaders()),
                "body": base64.b64encode(body).decode()}
    finally:
        conn.close()


if __name__ == "__main__":
    try:
        request_file = Path(sys.argv[1])
        payload = json.loads(request_file.read_text())
        request_file.unlink()
        print(json.dumps(run(payload)))
    except Exception as exc:
        print(json.dumps({"error": str(exc)}))
