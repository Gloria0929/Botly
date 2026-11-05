"""读取项目启动说明，在既有 Cube / Docker 沙箱管理预览后端。"""
import asyncio
import base64
import hashlib
import json
from pathlib import Path
import re
import shlex
from uuid import uuid4

from app.sandbox import files, runtime


def configuration(user_id: str, chat_id: str, entry: str) -> dict | None:
    root = files.chat_root(user_id, chat_id)
    output = root / "output"
    directory = files.safe_path(user_id, chat_id, entry).parent
    while directory.is_relative_to(output):
        manifest = directory / "preview.json"
        if manifest.is_file():
            manifest = files.safe_path(user_id, chat_id, manifest.relative_to(root).as_posix())
            data = json.loads(manifest.read_text())
            backend = data.get("backend")
            if backend:
                command = backend.get("command", "").strip()
                port = backend.get("port")
                api_prefix = backend.get("api_prefix", "/api").rstrip("/")
                if not command or len(command) > 4000:
                    raise ValueError("preview.json 缺少有效的 backend.command")
                if type(port) is not int or not 1024 <= port <= 65535 or port == 49983:
                    raise ValueError("preview.json 的 backend.port 必须是可用的应用端口（1024–65535）")
                if not re.fullmatch(r"/[a-zA-Z0-9_/-]+", api_prefix) or "//" in api_prefix:
                    raise ValueError("preview.json 的 api_prefix 必须是 /api 这样的路径前缀")
                relative = directory.relative_to(root) / backend.get("cwd", ".")
                cwd = files.safe_path(user_id, chat_id, relative.as_posix())
                if not cwd.is_dir():
                    raise ValueError("后端工作目录不存在，请先同步项目文件")
                key = hashlib.sha256(manifest.relative_to(root).as_posix().encode()).hexdigest()[:16]
                return {"command": command, "cwd": "/workspace/" + cwd.relative_to(root).as_posix(),
                    "port": port, "api_prefix": api_prefix,
                    "state_dir": f"/workspace/output/.preview/{key}",
                    "manifest": manifest.relative_to(root).as_posix()}
        directory = directory.parent
    return None


def command_for(request_path: str) -> str:
    source = Path(__file__).with_name("preview_runner.py").read_bytes()
    runner = base64.b64encode(source).decode()
    code = "import base64; exec(compile(base64.b64decode(" + repr(runner) + "), '<preview-runner>', 'exec'))"
    return "python3 -c " + shlex.quote(code) + " " + shlex.quote(request_path)


def _cube_call(connection, user_id, chat_id, request_path, payload, sync):
    from app.sandbox import cube
    driver = cube.CubeBackend(connection)
    box, session = driver._open(user_id, chat_id)
    try:
        if sync:
            driver._push(box, user_id, chat_id, session.files_manifest or {})
        cube.ensure_directory(box, str(Path(request_path).parent))
        box.files.write(request_path, payload)
        result = box.commands.run(command_for(request_path), timeout=45, cwd="/workspace")
        if result.exit_code != 0:
            raise ValueError((result.stderr or result.stdout or "预览后端执行失败")[-1000:])
        return result.stdout
    finally:
        try:
            box.files.remove(request_path)
        except Exception:
            pass  # runner 已读取并删除；异常路径也尽力清理临时请求。
        cube.close_client(box)


async def invoke(user_id: str, chat_id: str, config: dict, action: str, **kwargs) -> dict:
    from app.sandbox import cube, linux
    if not runtime.LINUX_SANDBOX_ENABLED:
        raise ValueError("管理员已停用沙箱")
    relative = f"output/.preview/requests/{uuid4().hex}.json"
    request_path = "/workspace/" + relative
    payload = json.dumps({"config": config, "action": action, **kwargs})
    connection = cube.connection_for(user_id)
    if connection:
        # 生命周期操作与文件同步共用群聊锁，请求本身并发转发。
        if action in {"start", "stop"}:
            async with cube.chat_lock(user_id, chat_id):
                raw = await asyncio.to_thread(_cube_call, connection, user_id, chat_id, request_path, payload, action == "start")
        else:
            raw = await asyncio.to_thread(_cube_call, connection, user_id, chat_id, request_path, payload, False)
    elif isinstance(runtime.user_backend(user_id), runtime.DockerBackend):
        name = await linux.ensure_container(user_id, chat_id)
        if not await linux._has_binary(name, "python3"):
            error = await linux._install(name, "python3")
            if error:
                raise ValueError(error)
        files.write_file(user_id, chat_id, relative, payload)
        try:
            raw, err, code = await linux._docker("exec", name, "bash", "-lc", command_for(request_path), timeout=45)
        finally:
            files.delete_file(user_id, chat_id, relative)
        if code:
            raise ValueError(err[-1000:] or "预览后端执行失败")
    else:
        raise ValueError("后端预览需要连接 Cube 或 Docker 沙箱")
    try:
        result = json.loads(raw.strip())
    except ValueError:
        raise ValueError("沙箱没有返回有效的后端状态，请确认已安装 python3")
    if result.get("error"):
        raise ValueError(result["error"])
    return result
