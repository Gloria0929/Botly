"""直连用户部署的 CubeSandbox，官方 SDK、持久实例和工作区同步。"""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
import hashlib
import json
from pathlib import PurePosixPath
import shlex
from uuid import uuid4

import httpx
from cubesandbox import Config, Sandbox, TemplateInfo
from cubesandbox._exceptions import FilesystemNotFoundError, SandboxNotFoundError
from sqlalchemy import select

from app.core.config import CODE_SANDBOX_TIMEOUT, DATA_DIR, LINUX_SANDBOX_TIMEOUT
from app.core.config import DATABASE_URL
from app.core.db import IS_SQLITE, SessionLocal, session_scope
from app.core.models import Chat, CubeConnection, CubeSession, FileRec, now
from app.core.security import decrypt_secret
from app.sandbox import files
from app.sandbox.artifacts import is_artifact_file, is_artifact_path, is_output_file, is_skill_directory
from app.core.events import emit
from app.tools.registry import UNTRUSTED_PREFIX

WORKSPACE = "/workspace"
TERMINAL_ENV = {"TERM": "xterm-256color", "LANG": "C.UTF-8", "LC_ALL": "C.UTF-8", "COLUMNS": "100", "LINES": "30"}


def connection_for(user_id: str) -> CubeConnection | None:
    with SessionLocal() as db:
        return db.scalar(select(CubeConnection).where(CubeConnection.user_id == user_id))


def sdk_config(connection: CubeConnection) -> Config:
    return Config(api_url=connection.api_url, template_id=connection.template_id,
        api_key=decrypt_secret(connection.api_key_encrypted) if connection.api_key_encrypted else None,
        proxy_node_ip=connection.proxy_node_ip, proxy_port=connection.proxy_port,
        sandbox_domain=connection.sandbox_domain, request_timeout=30)


def fingerprint(connection: CubeConnection) -> str:
    # 环境身份只由后端和模板决定；密钥轮换、代理修正可继续连接原实例。
    return hashlib.sha256(json.dumps([connection.api_url, connection.template_id]).encode()).hexdigest()


def same_config(first: CubeConnection, second: CubeConnection) -> bool:
    fields = ("api_url", "template_id", "proxy_node_ip", "proxy_port", "sandbox_domain")
    return (all(getattr(first, name) == getattr(second, name) for name in fields)
        and decrypt_secret(first.api_key_encrypted) == decrypt_secret(second.api_key_encrypted))


def is_missing_process(exc: Exception | str | None) -> bool:
    message = str(exc).lower()
    missing = "not_found" in message or "not found" in message
    return (missing and ("process" in message or "pid" in message)) or (
        getattr(exc, "status_code", None) == 404
        and message.startswith(("connect failed:", "sendinput failed:", "update failed:"))
    )


def safe_error(exc: Exception, connection: CubeConnection) -> str:
    if isinstance(exc, SandboxNotFoundError):
        return "沙箱已删除或过期，请重新连接以创建新沙箱。"
    if is_missing_process(exc) or str(exc) == "终端连接已关闭":
        return "终端会话已结束或沙箱已失效，请重新连接。"
    message = str(exc)
    if connection.api_key_encrypted:
        key = decrypt_secret(connection.api_key_encrypted)
        if key:
            message = message.replace(key, "[密钥已隐藏]")
    return f"{type(exc).__name__}: {message[:250]}"


async def list_templates(connection: CubeConnection) -> list[dict]:
    headers = {}
    if connection.api_key_encrypted:
        key = decrypt_secret(connection.api_key_encrypted)
        if key:
            headers["X-API-Key"] = key
    # SDK 0.7.3 的 Template.list 未设置超时，也未关闭 Session；使用同一 REST 接口。
    async with httpx.AsyncClient(timeout=15) as client:
        response = await client.get(f"{connection.api_url}/templates", headers=headers)
        response.raise_for_status()
        data = response.json()
    if isinstance(data, dict):
        data = data.get("templates", data.get("items"))
    if not isinstance(data, list) or any(not isinstance(item, dict) for item in data):
        raise ValueError("后端未返回有效的模板列表")
    templates = []
    for item in data:
        info = TemplateInfo.from_dict(item)
        if info.template_id:
            templates.append({"id": info.template_id, "name": info.name or item.get("name") or "",
                "status": info.status})
    return templates


def close_client(sandbox: Sandbox) -> None:
    # SDK __exit__ 会 kill；仅关闭传输，远端实例保留给下次执行。
    sandbox._session.close()
    if sandbox._client is not None:
        sandbox._client.close()


def prepare_client(sandbox: Sandbox) -> None:
    # 固定 SDK 0.7.3 的 data-plane 客户端没有自动附加 API Key。
    # 显式加入后，启用鉴权的自部署 CubeProxy 也能接收命令和文件请求。
    if sandbox._client is None:
        sandbox._client = sandbox._build_data_client()
    if sandbox._config.api_key:
        sandbox._client.headers["X-API-Key"] = sandbox._config.api_key
    # 0.7.3 的 kill 等会话方法未指定 HTTP 超时；统一补齐，避免断开一直挂起。
    if hasattr(sandbox._session, "request") and not getattr(sandbox._session, "_botly_timeout", False):
        original = sandbox._session.request
        def request(method, url, **kwargs):
            kwargs.setdefault("timeout", 30)
            return original(method, url, **kwargs)
        sandbox._session.request = request
        sandbox._session._botly_timeout = True


def read_bytes(sandbox: Sandbox, path: str) -> bytes:
    # 0.7.3 files.read 只返回文本；直接复用 SDK 的路由与鉴权客户端读取原始字节。
    prepare_client(sandbox)
    headers = sandbox.files._base_headers()
    with sandbox._client.stream("GET", f"http://{sandbox.get_host(49983)}/files",
        params={"path": path, "username": "root"}, headers=headers, timeout=30) as response:
        response.raise_for_status()
        chunks, size = [], 0
        for chunk in response.iter_bytes():
            size += len(chunk)
            if size > 10 * 1024 * 1024:
                raise ValueError("Cube 产物超过单文件 10 MB 限制")
            chunks.append(chunk)
        return b"".join(chunks)


def ensure_directory(sandbox: Sandbox, path: str) -> None:
    # Cube 的 MakeDir 对已存在目录返回 already_exists；模板通常自带 /workspace。
    if sandbox.files.exists(path):
        return
    parent = str(PurePosixPath(path).parent)
    if parent != path:
        ensure_directory(sandbox, parent)
    sandbox.files.make_dir(path)


def delete_remote_file(connection: CubeConnection, sandbox_id: str, path: str) -> None:
    """只连接已有实例删除产物；远端实例或文件已不存在时视为完成。"""
    if not is_artifact_path(path):
        raise ValueError("只能删除工作区内的产物文件")
    try:
        sandbox = Sandbox.connect(sandbox_id, config=sdk_config(connection), timeout=3600)
    except SandboxNotFoundError:
        return
    try:
        prepare_client(sandbox)
        remote = str(PurePosixPath(WORKSPACE) / path)
        try:
            entry = sandbox.files.stat(remote)
            kind = str(entry.get("type", "")).lower().removeprefix("file_type_")
            if kind not in ("file", "regular"):
                raise ValueError("目标已不是普通文件，请在终端中处理")
            sandbox.files.remove(remote)
        except FilesystemNotFoundError:
            pass
    finally:
        close_client(sandbox)


def probe(connection: CubeConnection) -> dict:
    sandbox = None
    try:
        sandbox = Sandbox.create(config=sdk_config(connection), timeout=120)
        prepare_client(sandbox)
        ensure_directory(sandbox, WORKSPACE)
        result = sandbox.commands.run("printf BOTLY_CUBE_READY", timeout=15, cwd=WORKSPACE, envs=TERMINAL_ENV)
        if result.exit_code != 0 or "BOTLY_CUBE_READY" not in result.stdout:
            raise RuntimeError("沙箱命令执行未返回预期结果")
        path = f"{WORKSPACE}/.botly-probe-{uuid4().hex}.txt"
        sandbox.files.write(path, "BOTLY_FILE_READY")
        if sandbox.files.read(path) != "BOTLY_FILE_READY":
            raise RuntimeError("沙箱文件读写验证失败")
        return {"ok": True, "message": "已连接 Cube 后端，模板、命令执行和文件读写均可用"}
    finally:
        if sandbox is not None:
            try:
                sandbox.kill()
            finally:
                close_client(sandbox)


@asynccontextmanager
async def chat_lock(user_id: str, chat_id: str):
    """同群命令与文件同步串行；跨 Worker 复用数据库锁或 SQLite 文件锁。"""
    key = user_id + ":" + chat_id
    if not IS_SQLITE:
        from psycopg import AsyncConnection
        async with await AsyncConnection.connect(DATABASE_URL.replace("postgresql+psycopg://", "postgresql://", 1), autocommit=True) as conn:
            acquired = False
            try:
                while not acquired:
                    cur = await conn.execute("SELECT pg_try_advisory_lock(782341995, hashtext(%s))", (key,))
                    acquired = bool((await cur.fetchone())[0])
                    if not acquired:
                        await asyncio.sleep(.05)
                yield
            finally:
                if acquired:
                    await conn.execute("SELECT pg_advisory_unlock(782341995, hashtext(%s))", (key,))
    else:
        import fcntl
        directory = DATA_DIR / "sandbox-locks"
        directory.mkdir(exist_ok=True)
        with (directory / (hashlib.sha256(key.encode()).hexdigest() + ".lock")).open("a") as lock:
            while True:
                try:
                    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    await asyncio.sleep(.05)
            try:
                yield
            finally:
                fcntl.flock(lock, fcntl.LOCK_UN)


class CubeBackend:
    def __init__(self, connection: CubeConnection):
        self.connection = connection

    def _open(self, user_id: str, chat_id: str):
        if user_id != self.connection.user_id:
            raise ValueError("沙箱连接不属于当前账号")
        with SessionLocal() as db:
            chat = db.get(Chat, chat_id)
            if not chat or chat.user_id != user_id:
                raise ValueError("群聊不存在")
            existing = db.scalar(select(CubeSession).where(CubeSession.user_id == user_id, CubeSession.chat_id == chat_id))
        cfg = sdk_config(self.connection)
        digest = fingerprint(self.connection)
        if existing and existing.connection_hash != digest:
            raise ValueError("此群聊绑定的 Cube 配置已变更，请恢复原配置或使用新群聊")
        sandbox = None
        if existing:
            try:
                sandbox = Sandbox.connect(existing.sandbox_id, config=cfg, timeout=3600)
            except SandboxNotFoundError:
                pass  # 远端过期/删除后，新建并从本地文件区恢复。
        if sandbox is None:
            sandbox = Sandbox.create(config=cfg, timeout=3600, lifecycle={"on_timeout": "pause"},
                metadata={"botly_user": user_id, "botly_chat": chat_id})
            try:
                prepare_client(sandbox)
                ensure_directory(sandbox, WORKSPACE)
                # 终端重连也经过这里；从空清单恢复本地副本，不删除模板文件。
                # 全部上传成功后才替换会话记录，恢复失败仍保留旧记录和本地文件。
                restored_manifest = self._push(sandbox, user_id, chat_id, {})
                with session_scope() as db:
                    row = db.scalar(select(CubeSession).where(CubeSession.user_id == user_id, CubeSession.chat_id == chat_id))
                    if row is None:
                        row = CubeSession(user_id=user_id, chat_id=chat_id)
                        db.add(row)
                    row.sandbox_id, row.connection_hash, row.files_manifest, row.pty_pid = sandbox.sandbox_id, digest, restored_manifest, None
                    row.skills_manifest = {}
                existing = row
            except BaseException:
                sandbox.kill()
                close_client(sandbox)
                raise
        prepare_client(sandbox)
        try:
            self._sync_skills(sandbox, user_id, chat_id, existing.skills_manifest or {})
        except Exception:
            close_client(sandbox)
            raise
        return sandbox, existing

    def _sync_skills(self, sandbox, user_id, chat_id, previous):
        from app.services.skill_installation import library, managed_directory
        from app.tools.skills import _safe_rel

        current = {}
        directories = set()
        for skill_id, path, bundle in library(user_id):
            managed_directory(skill_id, path)
            old = previous.get(skill_id, {})
            digests = {rel: hashlib.sha256(data).hexdigest() for rel, data in bundle.items()}
            current[skill_id] = {"path": path, "files": digests}
            for rel, data in bundle.items():
                if old.get("path") == path and old.get("files", {}).get(rel) == digests[rel]:
                    continue
                remote = str(PurePosixPath(path) / rel)
                parent = str(PurePosixPath(remote).parent)
                if parent not in directories:
                    ensure_directory(sandbox, parent)
                    directories.add(parent)
                sandbox.files.write(remote, data)
            if old.get("path") == path:
                for rel in old.get("files", {}).keys() - bundle.keys():
                    if _safe_rel(rel) != rel:
                        raise ValueError("非法技能文件路径")
                    try:
                        sandbox.files.remove(str(PurePosixPath(path) / rel))
                    except FilesystemNotFoundError:
                        pass
        for skill_id, old in previous.items():
            if current.get(skill_id, {}).get("path") != old["path"]:
                managed_directory(skill_id, old["path"])
                try:
                    sandbox.files.remove(old["path"])
                except FilesystemNotFoundError:
                    pass
        if current != previous:
            with session_scope() as db:
                row = db.scalar(select(CubeSession).where(CubeSession.user_id == user_id, CubeSession.chat_id == chat_id))
                if not row or row.sandbox_id != sandbox.sandbox_id:
                    raise ValueError("沙箱会话已变化，请重新连接")
                row.skills_manifest = current

    async def sync_skills(self, user_id, chat_id):
        async with chat_lock(user_id, chat_id):
            task = asyncio.create_task(asyncio.to_thread(self._sync_existing_skills, user_id, chat_id))
            try:
                return await asyncio.shield(task)
            except asyncio.CancelledError:
                await asyncio.gather(task, return_exceptions=True)
                raise

    def _sync_existing_skills(self, user_id, chat_id):
        if user_id != self.connection.user_id:
            raise ValueError("沙箱连接不属于当前账号")
        with SessionLocal() as db:
            row = db.scalar(select(CubeSession).where(CubeSession.user_id == user_id, CubeSession.chat_id == chat_id))
        if not row:
            return False
        if row.connection_hash != fingerprint(self.connection):
            raise ValueError("Cube 配置已变更，请恢复原连接后安装技能")
        try:
            sandbox = Sandbox.connect(row.sandbox_id, config=sdk_config(self.connection), timeout=3600)
        except SandboxNotFoundError:
            return False
        try:
            prepare_client(sandbox)
            self._sync_skills(sandbox, user_id, chat_id, row.skills_manifest or {})
            return True
        finally:
            close_client(sandbox)

    def _push(self, sandbox, user_id, chat_id, manifest):
        current = {}
        for item in files.list_files(user_id, chat_id):
            rel = item["path"]
            if not is_artifact_file(user_id, chat_id, rel):
                continue
            local = files.safe_path(user_id, chat_id, rel)
            data = local.read_bytes()
            digest = hashlib.sha256(data).hexdigest()
            current[rel] = digest
            if manifest.get(rel) != digest:
                remote = str(PurePosixPath(WORKSPACE) / rel)
                ensure_directory(sandbox, str(PurePosixPath(remote).parent))
                sandbox.files.write(remote, data)
        for rel in manifest.keys() - current.keys():
            if is_artifact_path(rel):
                sandbox.files.remove(str(PurePosixPath(WORKSPACE) / rel))
        return current

    def _pull(self, sandbox, user_id, chat_id, old_manifest, bot_id=None, run_id=None):
        from app.sandbox.workspace import is_task_path, output_directory, workspace_for_run
        workspace = workspace_for_run(user_id, chat_id, run_id)
        output_dir = output_directory(workspace)
        scopes = (workspace, output_dir)
        manifest = {rel: digest for rel, digest in old_manifest.items()
                    if is_task_path(rel) and not any(rel.startswith(scope + "/") for scope in scopes)} if workspace else {}
        directories = [WORKSPACE]
        count = 0
        while directories:
            directory = directories.pop()
            entries = sandbox.files.list(directory)
            if directory != WORKSPACE and is_skill_directory(entry.get("name", "") for entry in entries):
                continue
            for entry in entries:
                count += 1
                if count > 2000:
                    raise ValueError("工作区文件超过 2000 项，请清理后重试")
                name = entry["name"]
                if not name or name in (".", "..") or "/" in name:
                    raise ValueError("Cube 返回了非法文件名")
                remote = PurePosixPath(directory) / name
                rel = str(remote.relative_to(WORKSPACE))
                if workspace and is_task_path(rel):
                    if not any(rel == scope or rel.startswith(scope + "/") or scope.startswith(rel + "/") for scope in scopes):
                        continue  # 不覆盖其他并行任务正在写入的本地文件。
                if not is_artifact_path(rel):
                    continue  # 技能、依赖、缓存留在远端，不扫描或同步到产物区。
                kind = str(entry.get("type", "")).lower().removeprefix("file_type_")
                if kind in ("directory", "dir"):
                    directories.append(str(remote))
                elif kind in ("file", "regular"):
                    # envd 的 protobuf int64 在 JSON 中是字符串。
                    if int(entry.get("size", 0) or 0) > 10 * 1024 * 1024:
                        raise ValueError(f"产物 {rel} 超过单文件 10 MB 限制")
                    data = read_bytes(sandbox, str(remote))
                    files.write_bytes(user_id, chat_id, rel, data)
                    manifest[rel] = hashlib.sha256(data).hexdigest()
                elif kind not in ("symlink", "symbolic_link"):
                    raise ValueError(f"Cube 返回了未知文件类型：{kind}")
                # 不下载远端符号链接，避免沙箱外文件被同步到本地。
        for rel in old_manifest.keys() - manifest.keys():
            if is_artifact_path(rel) and (not workspace or any(rel.startswith(scope + "/") for scope in scopes)):
                files.delete_file(user_id, chat_id, rel)
        with session_scope() as db:
            row = db.scalar(select(CubeSession).where(CubeSession.user_id == user_id, CubeSession.chat_id == chat_id))
            row.files_manifest = manifest
            for rec in db.scalars(select(FileRec).where(FileRec.user_id == user_id, FileRec.chat_id == chat_id)):
                if (is_artifact_path(rec.path) and rec.path in old_manifest and rec.path not in manifest
                        and (not workspace or any(rec.path.startswith(scope + "/") for scope in scopes))):
                    db.delete(rec)
            for rel, digest in manifest.items():
                if not is_output_file(user_id, chat_id, rel) or (workspace and not rel.startswith(output_dir + "/")):
                    continue
                rec = db.scalar(select(FileRec).where(FileRec.user_id == user_id, FileRec.chat_id == chat_id, FileRec.path == rel))
                if rec is None:
                    rec = FileRec(user_id=user_id, chat_id=chat_id, path=rel)
                    db.add(rec)
                rec.size = files.safe_path(user_id, chat_id, rel).stat().st_size
                if bot_id and old_manifest.get(rel) != digest:
                    rec.created_by_bot_id, rec.run_id = bot_id, run_id
            emit(db, user_id, chat_id, "files.updated", {"chat_id": chat_id})
        return manifest

    def _execute(self, user_id, chat_id, command, timeout, bot_id=None, run_id=None):
        from app.sandbox.workspace import workspace_for_run
        workspace = workspace_for_run(user_id, chat_id, run_id)
        workdir = f"{WORKSPACE}/{workspace}" if workspace else WORKSPACE
        sandbox, session = self._open(user_id, chat_id)
        try:
            ensure_directory(sandbox, workdir)
            pushed = self._push(sandbox, user_id, chat_id, session.files_manifest or {})
            # 先同步已有文件并登记 output 内容，基线用于识别本次 Agent 改动。
            baseline = self._pull(sandbox, user_id, chat_id, pushed, run_id=run_id)
            # 容器内 timeout 也约束命令，即使 HTTP 客户端取消也不会无限运行。
            wrapped = f"timeout -s KILL {timeout} bash -lc {shlex.quote(command)}"
            try:
                result = sandbox.commands.run(wrapped, cwd=workdir, timeout=timeout + 15, envs=TERMINAL_ENV)
                result.exit_code = int(result.exit_code)
                with session_scope() as db:
                    row = db.scalar(select(CubeSession).where(CubeSession.user_id == user_id, CubeSession.chat_id == chat_id))
                    row.last_executed_at, row.last_exit_code = now(), result.exit_code
            finally:
                self._pull(sandbox, user_id, chat_id, baseline, bot_id, run_id)
            combined = "\n".join(v.strip() for v in (result.stdout, result.stderr) if v.strip())
            return (UNTRUSTED_PREFIX + f"运行环境：Cube · 沙箱 {sandbox.sandbox_id}\n退出码 {result.exit_code}\n"
                + (combined[:20000] or "（无输出）"))
        finally:
            close_client(sandbox)

    async def warm(self, user_id, chat_id):
        # 远端按第一次执行懒创建，建群不消耗云资源。
        return None

    async def run_command(self, user_id, chat_id, command, *, bot_id=None, run_id=None):
        async with chat_lock(user_id, chat_id):
            return await self._run_locked(user_id, chat_id, command, LINUX_SANDBOX_TIMEOUT, bot_id, run_id)

    async def run_code(self, user_id, chat_id, code, language, *, bot_id=None, run_id=None):
        runner = {"python": "python3", "node": "node", "bash": "bash", "sh": "sh"}.get(language)
        if not runner:
            raise ValueError("不支持的运行语言")
        # 代码放在 stdin，由固定解释器执行；不将代码插入 shell 命令本体。
        import base64
        payload = base64.b64encode(code.encode()).decode()
        command = f"printf %s {shlex.quote(payload)} | base64 -d | {runner}"
        async with chat_lock(user_id, chat_id):
            return await self._run_locked(user_id, chat_id, command, CODE_SANDBOX_TIMEOUT, bot_id, run_id)

    async def _run_locked(self, user_id, chat_id, command, timeout, bot_id=None, run_id=None):
        task = asyncio.create_task(asyncio.to_thread(self._execute, user_id, chat_id, command, timeout, bot_id, run_id))
        try:
            return await asyncio.shield(task)
        except asyncio.CancelledError:
            # Python 线程无法取消，必须等其同步和收尾后再释放跨进程锁。
            await asyncio.gather(task, return_exceptions=True)
            raise
        except Exception as exc:
            return "Cube 沙箱执行失败：" + safe_error(exc, self.connection)

    async def release(self, user_id, chat_id):
        async with chat_lock(user_id, chat_id):
            await asyncio.to_thread(self._release, user_id, chat_id)

    def _release(self, user_id, chat_id, delete_record=True):
        with SessionLocal() as db:
            row = db.scalar(select(CubeSession).where(CubeSession.user_id == user_id, CubeSession.chat_id == chat_id))
        if not row:
            return
        if row.connection_hash != fingerprint(self.connection):
            raise ValueError("Cube 连接已变更，无法在新后端清理旧实例")
        sandbox = None
        try:
            sandbox = Sandbox.connect(row.sandbox_id, config=sdk_config(self.connection))
            prepare_client(sandbox)
            sandbox.kill()
        except SandboxNotFoundError:
            pass
        except Exception as exc:
            raise ValueError(safe_error(exc, self.connection)) from None
        finally:
            if sandbox is not None:
                close_client(sandbox)
        if delete_record:
            with session_scope() as db:
                row = db.get(CubeSession, row.id)
                if row:
                    db.delete(row)
