"""真实沙箱命令记录；用户手动命令由 API 直接执行，Agent 命令由 Worker 执行。"""
import asyncio
from datetime import timedelta
import re

from sqlalchemy import select, update

from app.core.db import SessionLocal, session_scope
from app.core.events import emit
from app.core.models import Bot, TerminalCommand, User, now
from app.sandbox import runtime
from app.sandbox import files as sandbox_files
from app.tools.registry import UNTRUSTED_PREFIX


def out(row, db):
    bot = db.get(Bot, row.bot_id) if row.bot_id else None
    return {"id": row.id, "actor": bot.name if bot else "你", "bot_id": row.bot_id,
        "kind": row.kind, "language": row.language, "command": row.command, "output": row.output,
        "status": row.status, "created_at": row.created_at.isoformat(),
        "started_at": row.started_at.isoformat() if row.started_at else None,
        "ended_at": row.ended_at.isoformat() if row.ended_at else None}


def changed(db, row):
    emit(db, row.user_id, row.chat_id, "terminal.updated", {"chat_id": row.chat_id, "command_id": row.id, "status": row.status})


def create(user_id, chat_id, command, kind="command", language="bash", bot_id=None, run_id=None, call_id=None):
    with session_scope() as db:
        if call_id and run_id:
            existing = db.scalar(select(TerminalCommand).where(TerminalCommand.run_id == run_id, TerminalCommand.tool_call_id == call_id))
            if existing:
                return existing.id
        row = TerminalCommand(user_id=user_id, chat_id=chat_id, command=command, kind=kind,
            language=language, bot_id=bot_id, run_id=run_id, tool_call_id=call_id,
            status="running" if bot_id else "queued", started_at=now() if bot_id else None)
        db.add(row)
        db.flush()
        changed(db, row)
        return row.id


def _prepare(command_id, claim):
    with session_scope() as db:
        row = db.get(TerminalCommand, command_id)
        if not row:
            return "命令不存在"
        if row.status in ("completed", "failed"):
            return UNTRUSTED_PREFIX + row.output
        if claim:
            result = db.execute(update(TerminalCommand).where(TerminalCommand.id == command_id,
                TerminalCommand.status == "queued").values(status="running", started_at=now(), lease_until=now()+timedelta(minutes=10)))
            if not result.rowcount:
                return None
            db.refresh(row)
            user = db.get(User, row.user_id)
            if not user or user.status != "active":
                row.status, row.output, row.ended_at = "failed", "账号不可用，未执行", now()
                changed(db, row)
                return row.output
            changed(db, row)
        return (row.user_id, row.chat_id, row.command, row.language, row.bot_id, row.run_id, row.kind)


async def execute(command_id, claim=False):
    values = await asyncio.to_thread(_prepare, command_id, claim)
    if not isinstance(values, tuple):
        return values
    user_id, chat_id, command, language, bot_id, run_id, kind = values
    try:
        if kind == "code":
            result = await runtime.run_code(user_id, chat_id, command, language, bot_id=bot_id, run_id=run_id)
        else:
            result = await runtime.run_command(user_id, chat_id, command, bot_id=bot_id, run_id=run_id)
    except BaseException as exc:
        result = "执行已中断" if isinstance(exc, asyncio.CancelledError) else f"沙箱执行失败：{type(exc).__name__}"
        if not isinstance(exc, asyncio.CancelledError):
            await asyncio.to_thread(sandbox_files.refresh_quota, user_id)
        await asyncio.to_thread(_finish, command_id, result, failed=True)
        if isinstance(exc, asyncio.CancelledError):
            raise
        return result
    await asyncio.to_thread(sandbox_files.refresh_quota, user_id)
    await asyncio.to_thread(_finish, command_id, result)
    return result


def _finish(command_id, result, failed=False):
    with session_scope() as db:
        row = db.get(TerminalCommand, command_id)
        if row:
            code = re.search(r"退出码\s+(-?\d+)", result)
            row.status = "failed" if failed or "失败：" in result or "未接入" in result or (code and code[1] != "0") else "completed"
            row.output = result.removeprefix(UNTRUSTED_PREFIX)[:30000]
            row.ended_at, row.lease_until = now(), None
            changed(db, row)


def pending():
    with session_scope() as db:
        # 用户命令中断后不自动重放，避免 apt / 删除等副作用再次发生。
        stale = db.scalars(select(TerminalCommand).where(TerminalCommand.bot_id.is_(None),
            TerminalCommand.status == "running", TerminalCommand.lease_until < now())).all()
        for row in stale:
            row.status, row.output, row.ended_at = "failed", "执行进程中断，请查看沙箱状态后重试", now()
            changed(db, row)
        return list(db.scalars(select(TerminalCommand.id).where(TerminalCommand.bot_id.is_(None),
            TerminalCommand.status == "queued").order_by(TerminalCommand.created_at).limit(20)))
