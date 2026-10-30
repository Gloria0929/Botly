"""用户设置：默认限额、通知开关、搜索服务 Key。"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..db import get_db
from ..deps import current_user
from ..models import User, UserSettings
from ..security import encrypt_secret

router = APIRouter(prefix="/settings", tags=["settings"])


class SettingsPatch(BaseModel):
    max_steps: int | None = None  # 兼容旧客户端，执行步数已不设限。
    token_budget: int | None = None
    notifications_enabled: bool | None = None
    approval_ttl_hours: int | None = None
    search_api_key: str | None = None
    search_provider: str | None = None


def _out(s: UserSettings) -> dict:
    return {
        "max_steps": 0,
        "token_budget": s.token_budget,
        "notifications_enabled": s.notifications_enabled,
        "approval_ttl_hours": s.approval_ttl_hours,
        "search_provider": s.search_provider,
        "search_configured": bool(s.search_api_key_encrypted),
    }


@router.get("")
def get_settings(user: User = Depends(current_user), db: Session = Depends(get_db)):
    s = db.get(UserSettings, user.id)
    if not s:
        s = UserSettings(user_id=user.id)
        db.add(s)
        db.flush()
    return _out(s)


@router.patch("")
def update_settings(
    payload: SettingsPatch, user: User = Depends(current_user), db: Session = Depends(get_db)
):
    s = db.get(UserSettings, user.id) or UserSettings(user_id=user.id)
    db.add(s)
    s.max_steps = 0
    if payload.token_budget is not None:
        if payload.token_budget != 0 and payload.token_budget < 1000:
            raise HTTPException(400, "token 预算应为 0（无限制）或至少 1000")
        s.token_budget = payload.token_budget
    if payload.notifications_enabled is not None:
        s.notifications_enabled = payload.notifications_enabled
    if payload.approval_ttl_hours is not None:
        s.approval_ttl_hours = max(1, min(168, payload.approval_ttl_hours))
    if payload.search_provider is not None:
        s.search_provider = payload.search_provider
    if payload.search_api_key is not None:
        s.search_api_key_encrypted = encrypt_secret(payload.search_api_key) if payload.search_api_key else None
    db.flush()
    return _out(s)
