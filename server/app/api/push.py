"""NTF-06 Web Push：VAPID 公钥下发与订阅管理。"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.deps import current_user
from app.core.models import PushSubscription, User, now
from app.services.push import keys, push_notification

router = APIRouter(prefix="/push", tags=["push"])


@router.get("/config")
def push_config(user: User = Depends(current_user)):
    """浏览器订阅前先拿 applicationServerKey。"""
    return {"enabled": True, "public_key": keys()["public"]}


@router.get("/subscriptions")
def list_subscriptions(
    user: User = Depends(current_user), db: Session = Depends(get_db)
):
    rows = db.scalars(
        select(PushSubscription)
        .where(PushSubscription.user_id == user.id)
        .order_by(PushSubscription.created_at.desc())
    ).all()
    return {
        "items": [
            {
                "id": s.id,
                "endpoint": s.endpoint,
                "user_agent": s.user_agent,
                "created_at": s.created_at.isoformat() if s.created_at else None,
            }
            for s in rows
        ]
    }


class SubscribeIn(BaseModel):
    endpoint: str = Field(min_length=1, max_length=2000)
    keys: dict[str, str]
    user_agent: str = Field(default="", max_length=300)


@router.post("/subscriptions")
def subscribe(
    payload: SubscribeIn,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    p256dh = (payload.keys or {}).get("p256dh", "").strip()
    auth = (payload.keys or {}).get("auth", "").strip()
    if not p256dh or not auth:
        raise HTTPException(422, "缺少 p256dh / auth 密钥")
    if not (payload.endpoint.startswith("https://") or payload.endpoint.startswith("http://localhost")):
        raise HTTPException(422, "推送端点必须是 https")
    row = db.scalar(
        select(PushSubscription).where(PushSubscription.endpoint == payload.endpoint)
    )
    if row:
        if row.user_id != user.id:
            # 端点被别的账号占用（基本不可能发生）：直接换绑给当前账号
            db.delete(row)
            row = None
        else:
            row.p256dh = p256dh
            row.auth = auth
            row.user_agent = payload.user_agent or row.user_agent
    if row is None:
        row = PushSubscription(
            user_id=user.id,
            endpoint=payload.endpoint,
            p256dh=p256dh,
            auth=auth,
            user_agent=payload.user_agent,
        )
        db.add(row)
    db.flush()
    return {"ok": True, "id": row.id}


class UnsubscribeIn(BaseModel):
    endpoint: str = Field(min_length=1, max_length=2000)


@router.post("/unsubscribe")
def unsubscribe(
    payload: UnsubscribeIn,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    row = db.scalar(
        select(PushSubscription).where(
            PushSubscription.endpoint == payload.endpoint,
            PushSubscription.user_id == user.id,
        )
    )
    if row:
        db.delete(row)
        db.flush()
    return {"ok": True}


@router.post("/test")
def test_push(user: User = Depends(current_user), db: Session = Depends(get_db)):
    """发一条测试通知，走完整链路（落库 + SSE + Web Push）。"""
    from app.core.events import notify

    n = notify(
        db,
        user.id,
        type_="push.test",
        title="Botly 测试推送",
        body="如果你看到了这条通知，说明 Web Push 已生效。",
    )
    return {"ok": True, "id": n.id}
