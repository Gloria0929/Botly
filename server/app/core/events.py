"""事件总线。

事件持久化并在提交后唤醒 SSE；审批邮件和通知同事务落库，Worker 后台投递。
"""
from __future__ import annotations


import logging
import hashlib
from concurrent.futures import ThreadPoolExecutor

from sqlalchemy import event, text
from sqlalchemy.orm import Session

from app.core import redis_bus
from app.core.models import Event, Notification, now

_redis_publisher = ThreadPoolExecutor(max_workers=2, thread_name_prefix="event-publish")
_EVENT_LOCK_NAMESPACE = 782341995


@event.listens_for(Session, "before_commit")
def _persist_queued_events(db: Session) -> None:
    queued = db.info.pop("pending_events", [])
    if not queued:
        return
    # 先完成业务写入，取得业务行锁后才在提交阶段分配事件 ID。
    # 锁只覆盖事件 INSERT 与提交，避免早拿事件锁、后等业务行锁形成死锁。
    db.flush()
    if db.get_bind().dialect.name == "postgresql":
        keys = sorted({int.from_bytes(hashlib.sha256(ev.user_id.encode()).digest()[:4],
                                     "big", signed=True) for ev in queued})
        for key in keys:
            db.execute(text("SELECT pg_advisory_xact_lock(:namespace, :key)"),
                       {"namespace": _EVENT_LOCK_NAMESPACE, "key": key})
    db.add_all(queued)
    db.flush()
    for ev in queued:
        db.info.setdefault("event_wakeups", {})[(ev.user_id, ev.chat_id)] = (
            ev.user_id, ev.chat_id, ev.id, ev.type,
        )


@event.listens_for(Session, "after_commit")
def _publish_committed_events(db: Session) -> None:
    for args in db.info.pop("event_wakeups", {}).values():
        _redis_publisher.submit(redis_bus.publish_event, *args)


@event.listens_for(Session, "after_rollback")
def _discard_event_wakeups(db: Session) -> None:
    db.info.pop("pending_events", None)
    db.info.pop("event_wakeups", None)


@event.listens_for(Session, "after_transaction_end")
def _discard_closed_events(db: Session, transaction) -> None:
    if transaction.parent is None:
        _discard_event_wakeups(db)


def emit(db: Session, user_id: str, chat_id: str | None, type_: str, payload: dict) -> Event:
    """事件与业务原子提交；返回对象的 id 在提交时才分配。"""
    ev = Event(user_id=user_id, chat_id=chat_id, type=type_, payload=payload)
    if not db.in_transaction():
        db.begin()
    db.flush()
    db.info.setdefault("pending_events", []).append(ev)
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
        email_status="pending" if type_ in ("approval", "approval_expired") else None,
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
    # NTF-06：页面关闭后通过 Service Worker + VAPID 投递；失败不影响通知主流程
    try:
        from app.services import push

        if type_ not in ("approval", "approval_expired"):
            push.push_notification(
                user_id,
                notif_id=n.id,
                type_=type_,
                title=title,
                body=body,
                chat_id=chat_id,
                target_type=target_type,
                target_id=target_id,
            )
    except Exception:  # noqa: BLE001
        logging.getLogger(__name__).exception("Web Push 调度失败")
    return n
