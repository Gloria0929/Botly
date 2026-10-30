"""页面内通知中心。"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import get_db
from ..deps import current_user
from ..models import Notification, User, now

router = APIRouter(prefix="/notifications", tags=["notifications"])


@router.get("")
def list_notifications(
    unread_only: bool = False,
    limit: int = 100,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    q = select(Notification).where(Notification.user_id == user.id)
    if unread_only:
        q = q.where(Notification.read_at.is_(None))
    rows = db.scalars(q.order_by(Notification.created_at.desc()).limit(limit)).all()
    unread = db.scalars(
        select(Notification).where(
            Notification.user_id == user.id, Notification.read_at.is_(None)
        )
    ).all()
    return {
        "items": [
            {
                "id": n.id,
                "type": n.type,
                "title": n.title,
                "body": n.body,
                "chat_id": n.chat_id,
                "target_type": n.target_type,
                "target_id": n.target_id,
                "read": n.read_at is not None,
                "created_at": n.created_at.isoformat() if n.created_at else None,
            }
            for n in rows
        ],
        "unread_count": len(unread),
    }


class ReadIn(BaseModel):
    ids: list[str] | None = None
    all: bool = False


@router.post("/read")
def mark_read(payload: ReadIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    if not payload.all and not payload.ids:
        return {"ok": True, "count": 0}
    q = select(Notification).where(
        Notification.user_id == user.id, Notification.read_at.is_(None)
    )
    if not payload.all and payload.ids:
        q = q.where(Notification.id.in_(payload.ids))
    rows = db.scalars(q).all()
    for n in rows:
        n.read_at = now()
    db.flush()
    return {"ok": True, "count": len(rows)}
