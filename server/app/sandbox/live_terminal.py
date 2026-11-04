"""持续连接 Cube PTY；键盘输入和终端输出直接转发，不扫描或同步文件。"""
from __future__ import annotations

import asyncio
import base64
from dataclasses import dataclass, field
import threading
import time

from cubesandbox import PtySize
from cubesandbox._exceptions import CubeSandboxError
from sqlalchemy import select, update

from app.core.db import session_scope
from app.core.logging_setup import get_logger
from app.core.models import CubeSession
from app.sandbox import cube

_sessions: dict[tuple[str, str], "LiveTerminal"] = {}
_locks: dict[tuple[str, str], asyncio.Lock] = {}
_HISTORY_LIMIT = 256 * 1024
log = get_logger("sandbox.terminal")


def _forget_pid(user_id, chat_id, sandbox_id, pid):
    try:
        with session_scope() as db:
            db.execute(update(CubeSession).where(
                CubeSession.user_id == user_id, CubeSession.chat_id == chat_id,
                CubeSession.sandbox_id == sandbox_id, CubeSession.pty_pid == pid,
            ).values(pty_pid=None))
    except Exception:
        log.warning("清理失效终端 PID 失败，重新连接时将再次检查")


@dataclass
class LiveTerminal:
    user_id: str
    chat_id: str
    connection: object
    sandbox: object
    handle: object
    loop: asyncio.AbstractEventLoop
    history: bytearray = field(default_factory=bytearray)
    subscribers: set = field(default_factory=set)
    closed: bool = False
    error: str | None = None
    last_active: float = field(default_factory=time.monotonic)
    writing: asyncio.Lock = field(default_factory=asyncio.Lock)

    def start_reader(self):
        def read():
            failure = None
            try:
                for chunk in self.handle:
                    self.loop.call_soon_threadsafe(self.publish, chunk)
                if self.handle.error:
                    failure = RuntimeError(str(self.handle.error))
            except Exception as exc:
                failure = exc
            finally:
                if cube.is_missing_process(failure) or self.handle.exit_code is not None:
                    _forget_pid(self.user_id, self.chat_id, self.sandbox.sandbox_id, self.handle.pid)
                error = cube.safe_error(failure, self.connection) if failure else None
                try:
                    self.loop.call_soon_threadsafe(self.finish, error)
                except RuntimeError:
                    pass
                cube.close_client(self.sandbox)
        threading.Thread(target=read, name=f"cube-pty-{self.chat_id[:8]}", daemon=True).start()

    def publish(self, data: bytes):
        if self.closed:
            return
        self.history.extend(data)
        if len(self.history) > _HISTORY_LIMIT:
            del self.history[:-_HISTORY_LIMIT]
        for queue in tuple(self.subscribers):
            if queue.full():
                self.subscribers.discard(queue)
                while not queue.empty():
                    queue.get_nowait()
                queue.put_nowait({"type": "error", "message": "终端输出过快，请重新连接"})
            else:
                queue.put_nowait(data)

    def finish(self, error):
        if self.closed:
            return
        self.closed, self.error = True, error
        event = {"type": "error", "message": error} if error else {"type": "exit", "code": self.handle.exit_code}
        for queue in tuple(self.subscribers):
            if queue.full():
                queue.get_nowait()
            queue.put_nowait(event)

    def subscribe(self):
        self.last_active = time.monotonic()
        queue = asyncio.Queue(maxsize=512)
        self.subscribers.add(queue)
        if self.history:
            queue.put_nowait(bytes(self.history))
        if self.closed:
            queue.put_nowait({"type": "error", "message": self.error} if self.error
                             else {"type": "exit", "code": self.handle.exit_code})
        return queue

    def unsubscribe(self, queue):
        self.subscribers.discard(queue)
        self.last_active = time.monotonic()

    async def process_gone(self, exc):
        self.finish(cube.safe_error(exc, self.connection))
        key = (self.user_id, self.chat_id)
        if _sessions.get(key) is self:
            _sessions.pop(key, None)

        def discard():
            _forget_pid(self.user_id, self.chat_id, self.sandbox.sandbox_id, self.handle.pid)
            cube.close_client(self.sandbox)
        try:
            await asyncio.to_thread(discard)
        except Exception:
            log.warning("关闭失效终端传输失败")

    async def input(self, data: str):
        if self.closed:
            raise RuntimeError("终端连接已关闭")
        self.last_active = time.monotonic()
        async with self.writing:
            try:
                await asyncio.to_thread(self.handle.send_stdin, data, request_timeout=10)
            except Exception as exc:
                if cube.is_missing_process(exc):
                    await self.process_gone(exc)
                raise

    async def resize(self, cols: int, rows: int):
        if not self.closed:
            async with self.writing:
                try:
                    await asyncio.to_thread(self.handle.resize, PtySize(rows=rows, cols=cols), request_timeout=10)
                except Exception as exc:
                    if cube.is_missing_process(exc):
                        await self.process_gone(exc)
                    raise


def _open(connection, user_id, chat_id, cols, rows):
    sandbox, row = cube.CubeBackend(connection)._open(user_id, chat_id)
    # SDK 的 PTY 输入/调整尺寸接口未显式传 user，envd 仍需要 Basic root。
    sandbox._client.headers["Authorization"] = "Basic " + base64.b64encode(b"root:").decode()
    try:
        handle = None
        if row.pty_pid:
            try:
                handle = sandbox.pty.connect(row.pty_pid, timeout=3600)
            except (CubeSandboxError, RuntimeError) as exc:
                # 已退出的 shell 可新建；鉴权、连接等错误不能悄悄覆盖。
                if not cube.is_missing_process(exc) and getattr(exc, "status_code", None) != 404:
                    raise
                _forget_pid(user_id, chat_id, sandbox.sandbox_id, row.pty_pid)
        if handle is None:
            env = {**cube.TERMINAL_ENV,
                "PS1": r"\w\$ ",
                "PROMPT_COMMAND": "printf '\033]7;file://%s%s\007' \"$HOSTNAME\" \"$PWD\""}
            handle = sandbox.pty.create(PtySize(rows=rows, cols=cols), cwd=cube.WORKSPACE,
                envs=env, user="root", timeout=3600)
            try:
                with session_scope() as db:
                    saved = db.scalar(select(CubeSession).where(CubeSession.user_id == user_id, CubeSession.chat_id == chat_id))
                    saved.pty_pid = handle.pid
            except BaseException:
                sandbox.pty.kill(handle.pid, request_timeout=10)
                raise
        return sandbox, handle
    except BaseException:
        cube.close_client(sandbox)
        raise


async def connect(user_id: str, chat_id: str, cols: int, rows: int):
    key = (user_id, chat_id)
    lock = _locks.setdefault(key, asyncio.Lock())
    async with lock:
        connection = await asyncio.to_thread(cube.connection_for, user_id)
        if connection is None:
            raise ValueError("请先在设置中连接 Cube 沙箱")
        previous = _sessions.get(key)
        if previous and not previous.closed:
            if not cube.same_config(previous.connection, connection):
                raise ValueError("Cube 配置已变更，请先断开旧终端")
            try:
                await previous.resize(cols, rows)
            except Exception as exc:
                if not cube.is_missing_process(exc):
                    raise
            else:
                if not previous.closed:
                    return previous
        try:
            async with cube.chat_lock(user_id, chat_id):
                sandbox, handle = await asyncio.to_thread(_open, connection, user_id, chat_id, cols, rows)
        except Exception as exc:
            raise ValueError(cube.safe_error(exc, connection)) from None
        terminal = LiveTerminal(user_id, chat_id, connection, sandbox, handle, asyncio.get_running_loop())
        _sessions[key] = terminal
        terminal.start_reader()
        return terminal


async def close_all():
    for terminal in tuple(_sessions.values()):
        if not terminal.closed:
            try:
                # 关闭传输而非杀远端 shell；重启后通过持久化 PID 恢复。
                await asyncio.to_thread(cube.close_client, terminal.sandbox)
            except Exception:
                pass
    _sessions.clear()
    _locks.clear()


async def reap_idle():
    while True:
        await asyncio.sleep(60)
        for key, terminal in tuple(_sessions.items()):
            if not terminal.subscribers and time.monotonic() - terminal.last_active > 600:
                _sessions.pop(key, None)
                _locks.pop(key, None)
                if not terminal.closed:
                    await asyncio.to_thread(cube.close_client, terminal.sandbox)
