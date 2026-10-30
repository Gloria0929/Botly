"""请求依赖：当前用户、CSRF 校验。"""
from __future__ import annotations

from fastapi import Depends, Header, HTTPException, Request, status
from sqlalchemy.orm import Session

from .config import SESSION_COOKIE
from .db import get_db
from .logging_setup import fingerprint, get_logger
from .models import User
from .security import read_session

CSRF_HEADER = "x-botly-client"
CSRF_VALUE = "web"

security_log = get_logger("security")


def _ip(request: Request) -> str:
    fwd = request.headers.get("x-forwarded-for")
    return fwd.split(",")[0].strip() if fwd else (request.client.host if request.client else "unknown")


def current_user(
    request: Request,
    db: Session = Depends(get_db),
    x_botly_client: str | None = Header(default=None),
) -> User:
    token = request.cookies.get(SESSION_COOKIE)
    user_id = read_session(token)
    if not user_id:
        security_log.warning("鉴权失败：未登录 %s %s ip=%s", request.method, request.url.path, _ip(request))
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "未登录")
    user = db.get(User, user_id)
    if not user or user.status != "active":
        security_log.warning(
            "鉴权失败：账号不可用或已删除 uid=%s %s %s ip=%s",
            fingerprint(user_id), request.method, request.url.path, _ip(request),
        )
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "未登录")
    # 写操作校验自定义请求头（配合 SameSite=Lax 防 CSRF）
    if request.method not in ("GET", "HEAD", "OPTIONS") and x_botly_client != CSRF_VALUE:
        security_log.warning(
            "CSRF 校验失败 uid=%s %s %s ip=%s", fingerprint(user.id), request.method, request.url.path, _ip(request),
        )
        raise HTTPException(status.HTTP_403_FORBIDDEN, "缺少 CSRF 校验头")
    return user


def owned_or_404(db: Session, model, obj_id: str, user_id: str):
    """按 user_id 取对象，越权与不存在都返回 404（ACC-05）。"""
    obj = db.get(model, obj_id)
    if not obj or getattr(obj, "user_id", None) != user_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "资源不存在")
    return obj
