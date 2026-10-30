"""事件总线。

第一版不引入 Redis：事件落 events 表，SSE 端点按 id 增量轮询，
因此 API 与 Worker 可以是两个独立进程，且天然支持 Last-Event-ID 断线补发。
"""
from __future__ import annotations


from sqlalchemy.orm import Session

from . import redis_bus
from .models import Event, Notification, now


def emit(db: Session, user_id: str, chat_id: str | None, type_: str, payload: dict) -> Event:
    ev = Event(user_id=user_id, chat_id=chat_id, type=type_, payload=payload)
    db.add(ev)
    db.flush()
    # 广播唤醒信号；SSE 端收到后从 events 表按 id 顺序读取，保证不丢不乱序
    redis_bus.publish_event(user_id, chat_id, ev.id, type_)
    return ev


def notify(
    db: Session,
    user_id: str,
    type_: str,
    title: str,
    body: str = "",
    chat_id: str | None = None,
    target_type: str | None = None,
    target_id: str | None = None,
) -> Notification:
    n = Notification(
        user_id=user_id,
        type=type_,
        title=title,
        body=body,
        chat_id=chat_id,
        target_type=target_type,
        target_id=target_id,
    )
    db.add(n)
    db.flush()
    emit(
        db,
        user_id,
        None,
        "notification.created",
        {
            "id": n.id,
            "type": type_,
            "title": title,
            "body": body,
            "chat_id": chat_id,
            "target_type": target_type,
            "target_id": target_id,
            "created_at": now().isoformat(),
        },
    )
    return n
