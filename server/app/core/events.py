"""事件总线。

事件持久化并在提交后唤醒 SSE；审批邮件和通知同事务落库，Worker 后台投递。
"""
from __future__ import annotations


import logging

from sqlalchemy import event
from sqlalchemy.orm import Session

from app.core import redis_bus
from app.core.models import Event, Notification, now


@event.listens_for(Session, "after_commit")
def _publish_committed_events(db: Session) -> None:
    for args in db.info.pop("event_wakeups", {}).values():
        redis_bus.publish_event(*args)


@event.listens_for(Session, "after_rollback")
def _discard_event_wakeups(db: Session) -> None:
    db.info.pop("event_wakeups", None)


def emit(db: Session, user_id: str, chat_id: str | None, type_: str, payload: dict) -> Event:
    ev = Event(user_id=user_id, chat_id=chat_id, type=type_, payload=payload)
    db.add(ev)
    db.flush()
    # 提交后再唤醒；提前发布会让 SSE 读不到尚未提交的片段，退回定时轮询。
    db.info.setdefault("event_wakeups", {})[(user_id, chat_id)] = (user_id, chat_id, ev.id, type_)
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
