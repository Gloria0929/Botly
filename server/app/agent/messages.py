"""持久化 Agent 消息并为每条线程分配单调序号。"""
from __future__ import annotations

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.core.events import emit
from app.core.models import Message, Thread, now


def _next_seq(db: Session, thread_id: str) -> int:
    """使用线程计数器原子分配序号，旧 SQLite 不支持 RETURNING 时退回 MAX。"""
    value = db.execute(
        update(Thread)
        .where(Thread.id == thread_id)
        .values(seq_counter=func.coalesce(Thread.seq_counter, 0) + 1)
        .returning(Thread.seq_counter)
    ).scalar_one_or_none()
    if value is not None:
        return int(value)
    return (db.scalar(select(func.max(Message.seq)).where(Message.thread_id == thread_id)) or 0) + 1


def add_message(
    db: Session,
    *,
    thread: Thread,
    run_id: str | None,
    sender_type: str,
    type_: str,
    content: str = "",
    meta: dict | None = None,
    sender_bot_id: str | None = None,
    status: str = "done",
    emit_event: bool = True,
) -> Message:
    msg = Message(
        user_id=thread.user_id,
        thread_id=thread.id,
        run_id=run_id,
        seq=_next_seq(db, thread.id),
        sender_type=sender_type,
        sender_bot_id=sender_bot_id,
        type=type_,
        content=content,
        meta=meta or {},
        status=status,
    )
    db.add(msg)
    db.flush()
    if emit_event:
        emit(db, thread.user_id, thread.chat_id, "message.created", {
            "id": msg.id,
            "thread_id": msg.thread_id,
            "run_id": msg.run_id,
            "seq": msg.seq,
            "sender_type": msg.sender_type,
            "sender_bot_id": msg.sender_bot_id,
            "type": msg.type,
            "content": msg.content,
            "meta": msg.meta,
            "status": msg.status,
            "created_at": (msg.created_at or now()).isoformat(),
        })
    return msg
