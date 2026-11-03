"""沙箱接入边界：none（默认）、docker（本地测试）、external（云适配器）。

业务层只调用此模块，云服务 SDK 与生命周期由部署方的适配器负责。
"""
from __future__ import annotations

from functools import lru_cache
from importlib import import_module
from typing import Protocol

from app.core.config import CODE_SANDBOX_ENABLED, LINUX_SANDBOX_ENABLED, SANDBOX_ADAPTER, SANDBOX_PROVIDER
from app.core.logging_setup import get_logger

log = get_logger("sandbox")


class SandboxBackend(Protocol):
    async def warm(self, user_id: str, chat_id: str) -> None: ...
    async def release(self, user_id: str, chat_id: str) -> None: ...
    async def run_command(self, user_id: str, chat_id: str, command: str) -> str: ...
    async def run_code(self, user_id: str, chat_id: str, code: str, language: str) -> str: ...


class DockerBackend:
    """本机 Docker 测试实现，沿用既有挂载与执行方式。"""
    async def warm(self, user_id: str, chat_id: str) -> None:
        from app.sandbox import linux
        from app.services.skill_installation import sync_local_skills
        await sync_local_skills(user_id, chat_id)
        await linux.warm_container(user_id, chat_id)

    async def release(self, user_id: str, chat_id: str) -> None:
        from app.sandbox import linux
        await linux.stop_container(chat_id)

    async def run_command(self, user_id: str, chat_id: str, command: str) -> str:
        from app.sandbox import linux
        from app.services.skill_installation import sync_local_skills
        await sync_local_skills(user_id, chat_id)
        return await linux.run_command(user_id, chat_id, command)

    async def run_code(self, user_id: str, chat_id: str, code: str, language: str) -> str:
        from app.sandbox import linux
        from app.services.skill_installation import sync_local_skills
        await sync_local_skills(user_id, chat_id)
        return await linux.run_code(user_id, chat_id, code, language)


@lru_cache(maxsize=1)
def backend() -> SandboxBackend | None:
    if not LINUX_SANDBOX_ENABLED or SANDBOX_PROVIDER == "none":
        return None
    if SANDBOX_PROVIDER == "docker":
        return DockerBackend()
    if SANDBOX_PROVIDER != "external":
        raise ValueError("BOTLY_SANDBOX_PROVIDER 应为 none / docker / external")
    module, sep, name = SANDBOX_ADAPTER.partition(":")
    if not sep or not module or not name:
        raise ValueError("云沙箱需配置 BOTLY_SANDBOX_ADAPTER=模块:适配器类")
    driver = getattr(import_module(module), name)()
    for method in ("warm", "release", "run_command", "run_code"):
        if not callable(getattr(driver, method, None)):
            raise ValueError(f"沙箱适配器缺少 {method}")
    return driver


def status(user_id: str | None = None) -> dict:
    if user_id:
        from app.sandbox.cube import connection_for
        connection = connection_for(user_id)
        if connection:
            from sqlalchemy import func, select
            from app.core.db import SessionLocal
            from app.core.models import Chat, CubeSession
            from app.sandbox.cube import fingerprint
            with SessionLocal() as db:
                owned = (CubeSession.user_id == user_id, CubeSession.connection_hash == fingerprint(connection))
                session_count = db.scalar(select(func.count(CubeSession.id)).where(*owned)) or 0
                rows = db.execute(select(CubeSession, Chat.name).outerjoin(Chat,
                    (Chat.id == CubeSession.chat_id) & (Chat.user_id == user_id)).where(*owned)
                    .order_by(CubeSession.last_executed_at.desc().nulls_last(), CubeSession.created_at.desc()).limit(3)).all()
            return {"provider": "cube", "available": LINUX_SANDBOX_ENABLED, "local_test": False,
                "reason": "" if LINUX_SANDBOX_ENABLED else "管理员已停用沙箱", "api_url": connection.api_url,
                "template_id": connection.template_id, "proxy_node_ip": connection.proxy_node_ip or "",
                "proxy_port": connection.proxy_port, "sandbox_domain": connection.sandbox_domain,
                "api_key_configured": bool(connection.api_key_encrypted),
                "last_checked_at": connection.last_checked_at.isoformat() if connection.last_checked_at else None,
                "last_check_error": connection.last_check_error,
                "verification": "failed" if connection.last_check_error else "passed" if connection.last_checked_at else "untested",
                "session_count": int(session_count),
                "sessions": [{"chat_id": session.chat_id, "chat_name": name or "（已删除群聊）",
                    "sandbox_id": session.sandbox_id, "last_exit_code": session.last_exit_code,
                    "last_executed_at": session.last_executed_at.isoformat() if session.last_executed_at else None}
                    for session, name in rows]}
    try:
        ready = backend() is not None
        reason = "" if ready else "尚未接入沙箱服务"
    except Exception as exc:
        ready, reason = False, f"沙箱配置无效：{type(exc).__name__}"
    return {"provider": SANDBOX_PROVIDER, "available": ready, "local_test": SANDBOX_PROVIDER == "docker", "reason": reason}


def user_backend(user_id: str):
    from app.sandbox.cube import CubeBackend, connection_for
    connection = connection_for(user_id)
    return CubeBackend(connection) if connection and LINUX_SANDBOX_ENABLED else backend()


async def warm(user_id: str, chat_id: str) -> None:
    try:
        driver = user_backend(user_id)
        if driver:
            await driver.warm(user_id, chat_id)
    except Exception:
        log.exception("沙箱预热失败 chat=%s", chat_id)


async def release(user_id: str, chat_id: str) -> None:
    from app.sandbox.cube import CubeBackend, connection_for
    connection = connection_for(user_id)
    if connection:
        await CubeBackend(connection).release(user_id, chat_id)
        return
    try:
        driver = user_backend(user_id)
        if driver:
            await driver.release(user_id, chat_id)
    except Exception:
        log.exception("沙箱清理失败 chat=%s", chat_id)


async def run_command(user_id: str, chat_id: str, command: str, *, bot_id=None, run_id=None) -> str:
    driver = user_backend(user_id)
    if driver is None:
        return "沙箱尚未接入，请在设置页查看沙箱接入状态。"
    from app.sandbox.cube import CubeBackend
    if isinstance(driver, CubeBackend):
        return await driver.run_command(user_id, chat_id, command, bot_id=bot_id, run_id=run_id)
    return await driver.run_command(user_id, chat_id, command)


async def run_code(user_id: str, chat_id: str, code: str, language: str = "python", *, bot_id=None, run_id=None) -> str:
    if not CODE_SANDBOX_ENABLED:
        return "代码执行未启用。"
    driver = user_backend(user_id)
    if driver is None:
        return "沙箱尚未接入，请在设置页查看沙箱接入状态。"
    from app.sandbox.cube import CubeBackend
    if isinstance(driver, CubeBackend):
        return await driver.run_code(user_id, chat_id, code, language, bot_id=bot_id, run_id=run_id)
    return await driver.run_code(user_id, chat_id, code, language)
