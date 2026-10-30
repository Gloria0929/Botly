"""账号：邀请码注册、登录、退出、当前用户。"""
from __future__ import annotations

from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import delete, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..config import COOKIE_SECURE, SESSION_COOKIE, SESSION_MAX_AGE
from ..accounts import RETENTION_DAYS, disable_user
from ..db import get_db
from ..deps import current_user
from ..models import Bot, Chat, ChatMember, InviteCode, LoginAttempt, Thread, User, UserSettings, now
from ..security import hash_password, make_session, verify_password
from ..templates import default_bots
from ..logging_setup import fingerprint, get_logger

router = APIRouter(prefix="/auth", tags=["auth"])
log = get_logger("security")


def _ip(request: Request) -> str:
    fwd = request.headers.get("x-forwarded-for")
    return fwd.split(",")[0].strip() if fwd else (request.client.host if request.client else "unknown")

class RegisterIn(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)
    invite_code: str = Field(min_length=1)
    name: str | None = None


class LoginIn(BaseModel):
    email: EmailStr
    password: str


def _set_cookie(response: Response, user_id: str, request: Request) -> None:
    response.set_cookie(
        SESSION_COOKIE,
        make_session(user_id),
        max_age=SESSION_MAX_AGE,
        httponly=True,
        samesite="lax",
        secure=COOKIE_SECURE or request.url.scheme == "https",
        path="/",
    )


@router.post("/register", status_code=201)
def register(payload: RegisterIn, response: Response, request: Request, db: Session = Depends(get_db)):
    email = payload.email.lower().strip()
    if db.scalar(select(User).where(User.email == email)):
        raise HTTPException(400, "该邮箱已注册")

    code = db.scalar(select(InviteCode).where(InviteCode.code == payload.invite_code.strip()))
    if not code:
        raise HTTPException(400, "邀请码无效")
    if code.expires_at and code.expires_at < now():
        raise HTTPException(400, "邀请码已过期")
    if code.used_count >= code.max_uses:
        raise HTTPException(400, "邀请码已用完")

    user = User(
        email=email,
        password_hash=hash_password(payload.password),
        name=payload.name or email.split("@")[0],
        invite_code_id=code.id,
    )
    db.add(user)
    try:
        db.flush()
    except IntegrityError:
        # 并发注册同一邮箱时，唯一索引兜底；返回 400 而不是 500。
        db.rollback()
        raise HTTPException(400, "该邮箱已注册")
    # 原子占用一次邀请码，避免并发注册超过 max_uses（ACC-04）。
    used = db.execute(
        update(InviteCode)
        .where(InviteCode.id == code.id, InviteCode.used_count < InviteCode.max_uses)
        .values(used_count=InviteCode.used_count + 1)
    )
    if used.rowcount != 1:
        db.rollback()
        raise HTTPException(400, "邀请码已用完")

    db.add(UserSettings(user_id=user.id))

    bots: list[Bot] = []
    for spec in default_bots():
        bot = Bot(
            user_id=user.id,
            name=spec["name"],
            avatar=spec["avatar"],
            role=spec["role"],
            system_prompt=spec["system_prompt"],
            tools=spec["tools"],
            is_supervisor_template=spec.get("is_supervisor_template", False),
        )
        db.add(bot)
        bots.append(bot)
    db.flush()

    chat = Chat(user_id=user.id, name="第一个群聊", supervisor_bot_id=bots[0].id)
    db.add(chat)
    db.flush()
    for b in bots:
        db.add(ChatMember(chat_id=chat.id, bot_id=b.id, user_id=user.id))
    db.add(Thread(user_id=user.id, chat_id=chat.id, type="main", title="主线程"))
    db.flush()

    _set_cookie(response, user.id, request)
    log.info("注册成功 uid=%s email=%s ip=%s", fingerprint(user.id), email, _ip(request))
    return {"id": user.id, "email": user.email, "name": user.name, "chat_id": chat.id}


@router.post("/login")
def login(payload: LoginIn, response: Response, request: Request, db: Session = Depends(get_db)):
    email = payload.email.lower().strip()
    ip = request.client.host if request.client else "unknown"
    cutoff = now() - timedelta(minutes=15)
    for condition in (LoginAttempt.email == email, LoginAttempt.ip == ip):
        failures = db.scalars(select(LoginAttempt).where(condition, LoginAttempt.created_at >= cutoff)
                              .order_by(LoginAttempt.created_at.desc()).limit(5)).all()
        if len(failures) >= 5:
            retry = max(1, int((failures[0].created_at + timedelta(minutes=15) - now()).total_seconds()) + 1)
            log.warning("登录被限流 email=%s ip=%s 重试=%ds", email, ip, retry)
            raise HTTPException(429, "连续登录失败过多，请在 15 分钟后重试", headers={"Retry-After": str(retry)})
    user = db.scalar(select(User).where(User.email == email))
    if not user or not verify_password(payload.password, user.password_hash):
        db.add(LoginAttempt(email=email, ip=ip))
        db.execute(delete(LoginAttempt).where(LoginAttempt.created_at < cutoff))
        # 错误响应会回滚依赖里的事务，计数需要先单独提交。
        db.commit()
        log.warning("登录失败 email=%s ip=%s 原因=%s", email, ip, "账号存在" if user else "账号不存在")
        raise HTTPException(401, "邮箱或密码错误")
    if user.status != "active":
        log.warning("登录失败 uid=%s email=%s ip=%s 原因=账号不可用", fingerprint(user.id), email, ip)
        raise HTTPException(403, "账号不可用")
    # 只清掉该邮箱的失败记录；成功后清掉整个 IP 会让攻击者用任意账号
    # 重置同一 IP 的失败计数，绕过 ACC-03。
    db.execute(delete(LoginAttempt).where(LoginAttempt.email == email))
    _set_cookie(response, user.id, request)
    log.info("登录成功 uid=%s email=%s ip=%s", fingerprint(user.id), email, ip)
    return {"id": user.id, "email": user.email, "name": user.name}


@router.post("/logout")
def logout(response: Response):
    response.delete_cookie(SESSION_COOKIE, path="/")
    return {"ok": True}


@router.get("/me")
def me(user: User = Depends(current_user), db: Session = Depends(get_db)):
    settings = db.get(UserSettings, user.id)
    has_provider = bool(
        db.scalar(select(Bot).where(Bot.user_id == user.id, Bot.provider_id.isnot(None), Bot.deleted_at.is_(None)))
    )
    return {
        "id": user.id,
        "email": user.email,
        "name": user.name,
        "has_provider": has_provider,
        "settings": {
            "max_steps": 0,
            "token_budget": settings.token_budget if settings else 300000,
            "notifications_enabled": settings.notifications_enabled if settings else True,
            "approval_ttl_hours": settings.approval_ttl_hours if settings else 24,
            "search_configured": bool(settings.search_api_key_encrypted) if settings else False,
        },
    }


class PasswordIn(BaseModel):
    current_password: str
    new_password: str = Field(min_length=8, max_length=128)


@router.post("/password")
def change_password(
        payload: PasswordIn, user: User = Depends(current_user), db: Session = Depends(get_db)
):
    if not verify_password(payload.current_password, user.password_hash):
        raise HTTPException(400, "当前密码不正确")
    user.password_hash = hash_password(payload.new_password)
    db.flush()
    return {"ok": True}


class DeleteAccountIn(BaseModel):
    password: str
    # 要求重新输入邮箱，避免误点；不做成“输入 DELETE”之类的仪式性文案
    confirm_email: str


@router.delete("/account")
def delete_account(
        payload: DeleteAccountIn,
        response: Response,
        user: User = Depends(current_user),
        db: Session = Depends(get_db),
):
    """ACC-07：删除账号。先停用、保留 30 天，到期由 Worker 彻底清除。"""
    if not verify_password(payload.password, user.password_hash):
        raise HTTPException(400, "密码不正确")
    if payload.confirm_email.strip().lower() != user.email:
        raise HTTPException(400, "确认邮箱与当前账号不一致")
    disable_user(db, user)
    response.delete_cookie(SESSION_COOKIE, path="/")
    log.warning("账号删除 uid=%s email=%s 保留 %d 天，至 %s",
                fingerprint(user.id), user.email, RETENTION_DAYS, user.purge_after)
    return {
        "ok": True,
        "purge_after": user.purge_after.isoformat() if user.purge_after else None,
        "retention_days": RETENTION_DAYS,
    }
