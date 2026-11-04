"""SSE 事件流。

事件本身持久化在 events 表（可重放）；Redis Pub/Sub 只负责「有新事件」的唤醒，
因此断线重连可以按 Last-Event-ID 精确补齐，Redis 重启也不会丢消息。
"""
from __future__ import annotations

import json
import asyncio
import time

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from sqlalchemy import func, or_, select

from app.core.db import SessionLocal
from app.core.deps import current_user
from app.core.models import Chat, Event, User
from app.core.redis_bus import Waker

router = APIRouter(tags=["events"])

HEARTBEAT_SECONDS = 15
FALLBACK_SECONDS = 10


def _sse(event_id: int, event_type: str, payload: dict) -> str:
    return f"id: {event_id}\nevent: {event_type}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"


async def _stream(user_id: str, chat_id: str | None, last_id: int, request: Request):
    # 空会话也立即输出首帧，让代理刷新响应头，EventSource 能马上进入 OPEN。
    yield ": connected\n\n"
    last_ping = time.monotonic()
    last_query = 0.0
    query_due = True
    async with Waker(user_id) as waker:
        while True:
            if await request.is_disconnected():
                break
            if query_due:
                rows = await asyncio.to_thread(_read_events, user_id, chat_id, last_id)
                last_query = time.monotonic()
                for e in rows:
                    last_id = e.id
                    yield _sse(e.id, e.type, e.payload or {})
                if rows:
                    query_due = True
                    continue
                query_due = False
            stamp = time.monotonic()
            timeout = max(0.0, min(last_query + FALLBACK_SECONDS - stamp,
                                   last_ping + HEARTBEAT_SECONDS - stamp))
            woke = await waker.wait(timeout=timeout)
            stamp = time.monotonic()
            query_due = woke or stamp - last_query >= FALLBACK_SECONDS
            if stamp - last_ping >= HEARTBEAT_SECONDS:
                last_ping = stamp
                yield ": ping\n\n"


def _read_events(user_id: str, chat_id: str | None, last_id: int):
    with SessionLocal() as db:
        q = select(Event).where(Event.id > last_id, Event.user_id == user_id)
        if chat_id:
            q = q.where(or_(Event.chat_id == chat_id, Event.chat_id.is_(None)))
        else:
            q = q.where(Event.chat_id.is_(None))
        return db.scalars(q.order_by(Event.id).limit(300)).all()


def _check_chat(user_id: str, chat_id: str) -> None:
    with SessionLocal() as db:
        chat = db.get(Chat, chat_id)
        if not chat or chat.user_id != user_id:
            raise HTTPException(404, "资源不存在")


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
    await asyncio.to_thread(_check_chat, user.id, chat_id)
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
    expected_user_id: str | None = Query(default=None),
):
    if expected_user_id is not None and expected_user_id != user.id:
        raise HTTPException(401, "登录账号已变化，请重新打开页面")
    if last_event_id is None and after is None:
        # 通知流给的是「实时提醒」，不是历史回放：浏览器通知中心本来就
        # 有一份完整历史（GET /notifications）。没有游标时从当前末尾开始，
        # 否则每次打开聊天页都会把旧审批重新弹一遍。
        start = await asyncio.to_thread(_latest_id, user.id, None)
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
