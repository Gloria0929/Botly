"""账号：邀请码注册、登录、退出、当前用户、找回密码与邮箱验证。"""
from __future__ import annotations

from datetime import timedelta
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import delete, func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .. import mail
from ..config import (
    COOKIE_SECURE,
    PUBLIC_BASE_URL,
    RESET_TOKEN_TTL_MINUTES,
    SESSION_COOKIE,
    SESSION_MAX_AGE,
    VERIFY_TOKEN_TTL_HOURS,
)
from ..accounts import RETENTION_DAYS, disable_user
from ..db import get_db
from ..deps import current_user
from ..models import (
    Bot,
    Chat,
    ChatMember,
    EmailToken,
    InviteCode,
    LoginAttempt,
    Thread,
    User,
    UserSettings,
    now,
)
from ..security import hash_password, make_session, random_token, token_hash, verify_password
from ..templates import default_bots
from ..logging_setup import fingerprint, get_logger

router = APIRouter(prefix="/auth", tags=["auth"])
log = get_logger("security")

# 同一邮箱的同类邮件：60 秒冷却，1 小时最多 5 封
_MAIL_COOLDOWN = timedelta(seconds=60)
_MAIL_HOURLY_CAP = 5


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


class ForgotIn(BaseModel):
    email: EmailStr


class ResetIn(BaseModel):
    token: str = Field(min_length=8, max_length=200)
    password: str = Field(min_length=8, max_length=128)


class TokenIn(BaseModel):
    token: str = Field(min_length=8, max_length=200)


def _set_cookie(response: Response, user_id: str, request: Request, epoch: int = 0) -> None:
    response.set_cookie(
        SESSION_COOKIE,
        make_session(user_id, epoch),
        max_age=SESSION_MAX_AGE,
        httponly=True,
        samesite="lax",
        secure=COOKIE_SECURE or request.url.scheme == "https",
        path="/",
    )


# --------------------------------------------------------------------------- #
# ACC-08：邮件令牌
# --------------------------------------------------------------------------- #
def _base_url(request: Request) -> str:
    """邮件里的链接前缀。优先用配置的公网地址，否则按请求头推导。"""
    if PUBLIC_BASE_URL:
        return PUBLIC_BASE_URL
    proto = (request.headers.get("x-forwarded-proto") or request.url.scheme or "http").split(",")[0].strip()
    host = request.headers.get("x-forwarded-host") or request.headers.get("host") or request.url.netloc
    return f"{proto}://{host}".rstrip("/")


def _issue_token(db: Session, user: User, kind: str, ttl: timedelta, request: Request) -> str:
    raw = random_token(32)
    db.add(
        EmailToken(
            user_id=user.id,
            email=user.email,
            kind=kind,
            token_hash=token_hash(raw),
            expires_at=now() + ttl,
            ip=_ip(request),
        )
    )
    db.flush()
    return raw


def _live_token(db: Session, raw: str, kind: str) -> EmailToken | None:
    row = db.scalar(
        select(EmailToken).where(
            EmailToken.token_hash == token_hash(raw), EmailToken.kind == kind
        )
    )
    if not row or row.used_at or row.expires_at <= now():
        return None
    return row


def _throttle(db: Session, email: str, kind: str, message: str = "请求过于频繁，请稍后再试") -> None:
    """同一邮箱的同类邮件限流，避免被当成发信机。"""
    since = now() - timedelta(hours=1)
    rows = db.scalars(
        select(EmailToken.created_at).where(
            EmailToken.email == email, EmailToken.kind == kind, EmailToken.created_at >= since
        )
    ).all()
    if rows:
        latest = max(rows)
        if now() - latest < _MAIL_COOLDOWN:
            raise HTTPException(429, message, headers={"Retry-After": "60"})
        if len(rows) >= _MAIL_HOURLY_CAP:
            raise HTTPException(429, message, headers={"Retry-After": "3600"})


def _dev_link(link: str) -> str | None:
    """没配 SMTP、且是开发模式时，把链接直接回给前端，方便本地走完整流程。"""
    return link if (mail.MAIL_DEV and not mail.configured()) else None


def _send(to: str, subject: str, text: str, html: str) -> bool:
    try:
        return mail.send(to, subject, text, html)
    except mail.MailNotConfigured:
        raise HTTPException(503, "邮件服务未配置，请联系管理员")
    except Exception as exc:  # noqa: BLE001 - SMTP 认证/超时等
        log.error("邮件发送失败 to=%s err=%s", to, exc)
        raise HTTPException(503, "邮件发送失败，请稍后重试")



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

    _set_cookie(response, user.id, request, user.session_epoch or 0)
    # ACC-08：注册后自动发一封验证邮件；没配 SMTP 时只落日志，绝不能挡住注册
    try:
        raw = _issue_token(db, user, "verify_email", timedelta(hours=VERIFY_TOKEN_TTL_HOURS), request)
        link = f"{_base_url(request)}/verify-email?token={quote(raw)}"
        subject, text, html = mail.verify_email_mail(link, VERIFY_TOKEN_TTL_HOURS)
        _send(user.email, subject, text, html)
    except HTTPException as exc:
        log.warning("注册验证邮件未发送 email=%s 原因=%s", email, exc.detail)
    except Exception as exc:  # noqa: BLE001
        log.warning("注册验证邮件未发送 email=%s err=%s", email, exc)

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
    _set_cookie(response, user.id, request, user.session_epoch or 0)
    log.info("登录成功 uid=%s email=%s ip=%s", fingerprint(user.id), email, ip)
    return {
        "id": user.id,
        "email": user.email,
        "name": user.name,
        "email_verified": bool(user.email_verified_at),
    }


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
        "email_verified": bool(user.email_verified_at),
        "email_verified_at": user.email_verified_at.isoformat() if user.email_verified_at else None,
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
        payload: PasswordIn,
        response: Response,
        request: Request,
        user: User = Depends(current_user),
        db: Session = Depends(get_db),
):
    if not verify_password(payload.current_password, user.password_hash):
        raise HTTPException(400, "当前密码不正确")
    user.password_hash = hash_password(payload.new_password)
    # 改密码同样让其他设备的旧会话失效，当前设备重新签发一张继续用
    user.session_epoch = (user.session_epoch or 0) + 1
    db.flush()
    _set_cookie(response, user.id, request, user.session_epoch)
    log.warning("密码已修改 uid=%s email=%s", fingerprint(user.id), user.email)
    return {"ok": True}


# --------------------------------------------------------------------------- #
# ACC-08：找回密码 / 邮箱验证
# --------------------------------------------------------------------------- #
@router.post("/forgot-password")
def forgot_password(payload: ForgotIn, request: Request, db: Session = Depends(get_db)):
    """申请重置链接。

    不管邮箱是否存在都返回同样的 `{"ok": true}`，避免被用来枚举账号；
    只有真的存在且发信成功时，才在开发模式下附带 dev_link 方便本地调试。
    """
    email = payload.email.lower().strip()
    _throttle(db, email, "reset_password")
    user = db.scalar(select(User).where(User.email == email, User.status == "active"))

    link: str | None = None
    if user:
        raw = _issue_token(db, user, "reset_password", timedelta(minutes=RESET_TOKEN_TTL_MINUTES), request)
        link = f"{_base_url(request)}/reset-password?token={quote(raw)}"
        subject, text, html = mail.password_reset_mail(link, RESET_TOKEN_TTL_MINUTES)
        _send(email, subject, text, html)
    else:
        # 不存在的邮箱也留一条记录：既让限流对两种请求一视同仁，
        # 又不会产生可用的令牌（expires_at == used_at == 现在）。
        db.add(
            EmailToken(
                user_id="",
                email=email,
                kind="reset_password",
                token_hash=token_hash(random_token(8)),
                expires_at=now(),
                used_at=now(),
                ip=_ip(request),
            )
        )
        db.flush()

    log.warning("申请重置密码 email=%s ip=%s 账号存在=%s", email, _ip(request), bool(user))
    out: dict = {"ok": True}
    if link:
        dev = _dev_link(link)
        if dev:
            out["dev_link"] = dev
    return out


@router.post("/reset-password")
def reset_password(payload: ResetIn, db: Session = Depends(get_db)):
    row = _live_token(db, payload.token, "reset_password")
    if not row:
        raise HTTPException(400, "链接无效或已过期，请重新申请")
    user = db.get(User, row.user_id)
    if not user or user.status != "active":
        raise HTTPException(400, "链接无效或已过期，请重新申请")

    user.password_hash = hash_password(payload.password)
    user.session_epoch = (user.session_epoch or 0) + 1
    # 同一个用户的其它重置链接一起作废，避免旧邮件还能改一次
    db.execute(
        update(EmailToken)
        .where(
            EmailToken.user_id == user.id,
            EmailToken.kind == "reset_password",
            EmailToken.used_at.is_(None),
        )
        .values(used_at=now())
    )
    db.flush()
    log.warning("密码已重置 uid=%s email=%s", fingerprint(user.id), user.email)
    return {"ok": True, "email": user.email}


@router.post("/send-verification")
def send_verification(
        request: Request,
        user: User = Depends(current_user),
        db: Session = Depends(get_db),
):
    if user.email_verified_at:
        return {"ok": True, "already": True, "email": user.email}
    _throttle(db, user.email, "verify_email", "验证邮件刚刚已经发送，请检查收件箱（含垃圾箱）")
    raw = _issue_token(db, user, "verify_email", timedelta(hours=VERIFY_TOKEN_TTL_HOURS), request)
    link = f"{_base_url(request)}/verify-email?token={quote(raw)}"
    subject, text, html = mail.verify_email_mail(link, VERIFY_TOKEN_TTL_HOURS)
    _send(user.email, subject, text, html)
    out: dict = {"ok": True, "already": False, "email": user.email}
    dev = _dev_link(link)
    if dev:
        out["dev_link"] = dev
    return out


@router.post("/verify-email")
def verify_email(payload: TokenIn, db: Session = Depends(get_db)):
    row = _live_token(db, payload.token, "verify_email")
    if not row:
        raise HTTPException(400, "链接无效或已过期，请重新发送验证邮件")
    user = db.get(User, row.user_id)
    if not user or user.status != "active":
        raise HTTPException(400, "链接无效或已过期，请重新发送验证邮件")
    if not user.email_verified_at:
        user.email_verified_at = now()
    row.used_at = now()
    db.flush()
    log.info("邮箱已验证 uid=%s email=%s", fingerprint(user.id), user.email)
    return {"ok": True, "email": user.email}


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
