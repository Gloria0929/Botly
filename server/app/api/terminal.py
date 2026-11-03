"""会话沙箱终端：仅当前用户可查看或手动执行。"""
from datetime import timedelta
import asyncio
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, HTTPException, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import LINUX_SANDBOX_ENABLED, SESSION_COOKIE
from app.core.db import IS_SQLITE, SessionLocal, get_db
from app.core.deps import current_user, owned_or_404
from app.core.models import Chat, CubeSession, Event, Run, TerminalCommand, User, now
from app.sandbox import runtime, terminal
from app.core.security import read_session

router = APIRouter(prefix="/chats", tags=["terminal"])


def _socket_user(token, chat_id):
    session = read_session(token)
    if session is None:
        raise ValueError("登录已失效，请重新登录")
    with SessionLocal() as db:
        user = db.get(User, session[0])
        chat = db.get(Chat, chat_id)
        if not user or user.status != "active" or (user.session_epoch or 0) != session[1]:
            raise ValueError("登录已失效，请重新登录")
        if not chat or chat.user_id != user.id:
            raise ValueError("会话不存在")
        return user.id


@router.websocket("/{chat_id}/terminal/ws")
async def live_socket(websocket: WebSocket, chat_id: str):
    # WebSocket 无法发送 CSRF 自定义头，用相同站点 Origin + 有效 Cookie 校验。
    origin = urlsplit(websocket.headers.get("origin", "")).netloc.lower()
    host = (websocket.headers.get("x-forwarded-host") or websocket.headers.get("host", "")).split(",", 1)[0].strip().lower()
    if not origin or origin != host:
        await websocket.close(code=1008)
        return
    await websocket.accept()
    live = None
    subscription = None
    tasks = []
    try:
        if not LINUX_SANDBOX_ENABLED:
            raise ValueError("管理员已停用沙箱")
        token = websocket.cookies.get(SESSION_COOKIE)
        user_id = await asyncio.to_thread(_socket_user, token, chat_id)
        cols = max(10, min(300, int(websocket.query_params.get("cols", "80"))))
        rows = max(3, min(150, int(websocket.query_params.get("rows", "24"))))
        from app.sandbox import live_terminal
        live = await live_terminal.connect(user_id, chat_id, cols, rows)
        subscription = live.subscribe()
        inputs = asyncio.Queue(maxsize=64)
        await websocket.send_json({"type": "ready", "sandbox_id": live.sandbox.sandbox_id, "pid": live.handle.pid})

        async def output():
            while True:
                message = await subscription.get()
                if isinstance(message, bytes):
                    await websocket.send_bytes(message)
                else:
                    await websocket.send_json(message)
                    return

        async def receive():
            while True:
                message = await websocket.receive_json()
                if not isinstance(message, dict):
                    raise ValueError("终端输入格式无效")
                if message.get("type") == "input":
                    data = message.get("data")
                    if not isinstance(data, str) or len(data.encode()) > 65536:
                        raise ValueError("单次终端输入超过限制")
                    for offset in range(0, len(data), 16384):
                        await inputs.put(data[offset:offset + 16384])
                elif message.get("type") == "resize":
                    await live.resize(max(10, min(300, int(message["cols"]))), max(3, min(150, int(message["rows"]))))

        async def authorize():
            while True:
                await asyncio.sleep(30)
                await asyncio.to_thread(_socket_user, token, chat_id)

        async def input_writer():
            # 接收键盘事件不等待每次 envd HTTP 往返；合并快速输入再写入 PTY。
            while True:
                data = await inputs.get()
                await asyncio.sleep(.008)
                while not inputs.empty() and len(data) < 16384:
                    data += inputs.get_nowait()
                await live.input(data)

        tasks = [asyncio.create_task(output()), asyncio.create_task(receive()),
            asyncio.create_task(input_writer()), asyncio.create_task(authorize())]
        done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        for task in done:
            task.result()
    except WebSocketDisconnect:
        pass
    except Exception as exc:
        from app.sandbox import cube
        error = cube.safe_error(exc, live.connection) if live else str(exc)[:250]
        try:
            await websocket.send_json({"type": "error", "message": error})
        except Exception:
            pass
    finally:
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        if live and subscription:
            live.unsubscribe(subscription)
        try:
            await websocket.close()
        except Exception:
            pass


@router.get("/{chat_id}/terminal")
def terminal_history(chat_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    owned_or_404(db, Chat, chat_id, user.id)
    cursor = db.scalar(select(func.max(Event.id)).where(Event.chat_id == chat_id, Event.user_id == user.id)) or 0
    rows = list(db.scalars(select(TerminalCommand).where(TerminalCommand.user_id == user.id,
        TerminalCommand.chat_id == chat_id).order_by(TerminalCommand.created_at.desc()).limit(100)))
    state = runtime.status(user.id)
    session = db.scalar(select(CubeSession).where(CubeSession.user_id == user.id, CubeSession.chat_id == chat_id))
    agent_running = db.scalar(select(Run.id).where(Run.user_id == user.id, Run.chat_id == chat_id,
        Run.status.in_(("running", "queued", "waiting_children"))).limit(1))
    return {"items": [terminal.out(row, db) for row in reversed(rows)], "provider": state["provider"],
        "available": state["available"], "reason": state.get("reason", ""), "workspace": "/workspace",
        "sandbox_id": session.sandbox_id if session else None, "agent_running": bool(agent_running), "event_cursor": cursor}


class CommandIn(BaseModel):
    command: str = Field(min_length=1, max_length=10000)


@router.post("/{chat_id}/terminal", status_code=201)
async def send_command(chat_id: str, payload: CommandIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    # 同群发送和终端命令共享群聊锁，避免并发请求绕过执行状态检查。
    if IS_SQLITE:
        db.connection().exec_driver_sql("BEGIN IMMEDIATE")
    chat = db.scalar(select(Chat).where(Chat.id == chat_id, Chat.user_id == user.id).with_for_update())
    if not chat:
        raise HTTPException(404, "资源不存在")
    if not payload.command.strip():
        raise HTTPException(400, "命令不能为空")
    state = runtime.status(user.id)
    if not state["available"]:
        raise HTTPException(409, state.get("reason") or "尚未接入沙箱")
    active = db.scalar(select(Run.id).where(Run.user_id == user.id, Run.chat_id == chat_id,
        Run.status.in_(("running", "queued", "waiting_children"))).limit(1))
    pending = db.scalar(select(TerminalCommand.id).where(TerminalCommand.user_id == user.id,
        TerminalCommand.chat_id == chat_id, TerminalCommand.status.in_(("queued", "running"))).limit(1))
    if active or pending:
        raise HTTPException(409, "当前会话正在执行，请等待完成后输入终端命令")
    # 手动命令由此请求直接执行，不等待 Worker 的定时队列扫描。
    # 先提交 running 记录，让其他标签和实时终端立即看到当前状态。
    row = TerminalCommand(user_id=user.id, chat_id=chat_id, command=payload.command.strip(),
        status="running", started_at=now(), lease_until=now()+timedelta(minutes=10))
    db.add(row)
    db.flush()
    terminal.changed(db, row)
    db.commit()
    await terminal.execute(row.id)
    db.refresh(row)
    return terminal.out(row, db)
