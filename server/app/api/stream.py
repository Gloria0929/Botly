"""SSE 事件流。

事件本身持久化在 events 表（可重放）；Redis Pub/Sub 只负责「有新事件」的唤醒，
因此断线重连可以按 Last-Event-ID 精确补齐，Redis 重启也不会丢消息。
"""
from __future__ import annotations

import json

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from sqlalchemy import func, select

from ..db import SessionLocal
from ..deps import current_user
from ..models import Chat, Event, User
from ..redis_bus import Waker

router = APIRouter(tags=["events"])

HEARTBEAT_SECONDS = 15


def _sse(event_id: int, event_type: str, payload: dict) -> str:
    return f"id: {event_id}\nevent: {event_type}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"


async def _stream(user_id: str, chat_id: str | None, last_id: int, request: Request):
    # 空会话也立即输出首帧，让代理刷新响应头，EventSource 能马上进入 OPEN。
    yield ": connected\n\n"
    idle = 0.0
    async with Waker(user_id) as waker:
        while True:
            if await request.is_disconnected():
                break
            with SessionLocal() as db:
                q = select(Event).where(Event.id > last_id, Event.user_id == user_id)
                if chat_id:
                    q = q.where(Event.chat_id == chat_id)
                else:
                    q = q.where(Event.chat_id.is_(None))
                rows = db.scalars(q.order_by(Event.id).limit(300)).all()
            for e in rows:
                last_id = e.id
                idle = 0.0
                yield _sse(e.id, e.type, e.payload or {})
            if rows:
                continue
            await waker.wait(timeout=1.0)
            idle += 1.0
            if idle >= HEARTBEAT_SECONDS:
                idle = 0.0
                yield ": ping\n\n"


def _initial_id(last_event_id: str | None, after: int | None) -> int:
    if last_event_id:
        try:
            return max(after or 0, int(last_event_id))
        except ValueError:
            pass
    return after or 0


def _latest_id(user_id: str, chat_id: str | None) -> int:
    with SessionLocal() as db:
        q = select(func.max(Event.id)).where(Event.user_id == user_id)
        q = q.where(Event.chat_id == chat_id) if chat_id else q.where(Event.chat_id.is_(None))
        return int(db.scalar(q) or 0)


@router.get("/chats/{chat_id}/events")
async def chat_events(
    chat_id: str,
    request: Request,
    after: int | None = Query(default=None),
    last_event_id: str | None = Header(default=None, alias="Last-Event-ID"),
    user: User = Depends(current_user),
):
    with SessionLocal() as s:
        chat = s.get(Chat, chat_id)
        if not chat or chat.user_id != user.id:
            raise HTTPException(404, "资源不存在")
    start = _initial_id(last_event_id, after)
    return StreamingResponse(
        _stream(user.id, chat_id, start, request),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/notifications/events")
async def notification_events(
    request: Request,
    after: int | None = Query(default=None),
    last_event_id: str | None = Header(default=None, alias="Last-Event-ID"),
    user: User = Depends(current_user),
):
    if last_event_id is None and after is None:
        # 通知流给的是「实时提醒」，不是历史回放：浏览器通知中心本来就
        # 有一份完整历史（GET /notifications）。没有游标时从当前末尾开始，
        # 否则每次打开聊天页都会把旧审批重新弹一遍。
        start = _latest_id(user.id, None)
    else:
        start = _initial_id(last_event_id, after)
    return StreamingResponse(
        _stream(user.id, None, start, request),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
