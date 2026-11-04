"""账号：邀请码注册、登录、退出、当前用户、找回密码与邮箱验证。"""
from __future__ import annotations

from datetime import timedelta
from urllib.parse import quote, urlsplit

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request, Response
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import delete, func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.services import mail
from app.services import oauth
from app.core.config import (
    COOKIE_SECURE,
    APP_ENV,
    DEVELOPMENT_ENVS,
    OAUTH_ALLOW_SIGNUP,
    OAUTH_REDIRECT_BASE,
    OAUTH_STATE_COOKIE,
    OAUTH_STATE_MAX_AGE,
    PUBLIC_BASE_URL,
    RESET_TOKEN_TTL_MINUTES,
    SESSION_COOKIE,
    SESSION_MAX_AGE,
    VERIFY_TOKEN_TTL_HOURS,
)
from app.services.accounts import RETENTION_DAYS, disable_user
from app.core.db import get_db
from app.core.deps import client_ip as _ip, current_user
from app.core.models import (
    Bot,
    Chat,
    ChatMember,
    EmailToken,
    InviteCode,
    LoginAttempt,
    OAuthAccount,
    Thread,
    User,
    UserSettings,
    now,
)
from app.core.security import (
    hash_password,
    make_oauth_state,
    make_session,
    random_token,
    read_oauth_state,
    read_session,
    token_hash,
    verify_password,
)
from app.services.templates import default_bots
from app.core.logging_setup import fingerprint, get_logger

router = APIRouter(prefix="/auth", tags=["auth"])
log = get_logger("security")

# 同一邮箱的同类邮件：60 秒冷却，1 小时最多 5 封
_MAIL_COOLDOWN = timedelta(seconds=60)
_MAIL_HOURLY_CAP = 5
_DUMMY_PASSWORD_HASH = hash_password(f"unused-{random_token(32)}")
_REGISTER_ATTEMPT_EMAIL = "__register__"


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
    """邮件只能使用部署地址；开发/测试时允许从请求推导。"""
    if PUBLIC_BASE_URL:
        try:
            parsed = urlsplit(PUBLIC_BASE_URL)
            valid = (parsed.scheme in {"http", "https"} and parsed.hostname and not parsed.username
                     and not parsed.password and not parsed.query and not parsed.fragment)
            parsed.port
        except ValueError:
            valid = False
        if not valid:
            raise HTTPException(503, "BOTLY_BASE_URL 必须是有效的 HTTP/HTTPS 站点地址")
        return PUBLIC_BASE_URL
    if APP_ENV not in DEVELOPMENT_ENVS:
        raise HTTPException(503, "非开发环境必须配置 BOTLY_BASE_URL")
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
    return link if (APP_ENV in DEVELOPMENT_ENVS and mail.MAIL_DEV and not mail.configured()) else None


def _throttle_mail_ip(db: Session, request: Request, kind: str) -> None:
    count = db.scalar(select(func.count(EmailToken.id)).where(
        EmailToken.ip == _ip(request), EmailToken.kind == kind,
        EmailToken.created_at >= now() - timedelta(hours=1),
    )) or 0
    if count >= 20:
        raise HTTPException(429, "请求过于频繁，请稍后再试", headers={"Retry-After": "3600"})


def _send(to: str, subject: str, text: str, html: str) -> bool:
    try:
        return mail.send(to, subject, text, html)
    except mail.MailNotConfigured:
        raise HTTPException(503, "邮件服务未配置，请联系管理员")
    except Exception as exc:  # noqa: BLE001 - SMTP 认证/超时等
        log.error("邮件发送失败 to=%s err=%s", to, exc)
        raise HTTPException(503, "邮件发送失败，请稍后重试")



def _default_workspace(db: Session, user: User) -> Chat:
    """新账号的初始空间：设置项 + 一组默认 Bot + 第一个群聊 + 主线程。

    邮箱注册与 OAuth 首次登录都走这里，保证两条路的起点一致。
    """
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
    return chat


@router.post("/register", status_code=201)
def register(payload: RegisterIn, response: Response, request: Request, background: BackgroundTasks, db: Session = Depends(get_db)):
    ip = _ip(request)
    cutoff = now() - timedelta(hours=1)
    db.add(LoginAttempt(email=_REGISTER_ATTEMPT_EMAIL, ip=ip))
    db.flush()
    attempts = db.scalar(select(func.count(LoginAttempt.id)).where(
        LoginAttempt.email == _REGISTER_ATTEMPT_EMAIL,
        LoginAttempt.ip == ip,
        LoginAttempt.created_at >= cutoff,
    )) or 0
    db.commit()
    if attempts > 10:
        raise HTTPException(429, "注册请求过于频繁，请稍后重试", headers={"Retry-After": "3600"})

    email = payload.email.lower().strip()
    if db.scalar(select(User).where(User.email == email)):
        raise HTTPException(400, "无法注册，请确认邮箱和邀请码")

    code = db.scalar(select(InviteCode).where(InviteCode.code == payload.invite_code.strip()))
    if not code:
        raise HTTPException(400, "无法注册，请确认邮箱和邀请码")
    if code.expires_at and code.expires_at < now():
        raise HTTPException(400, "无法注册，请确认邮箱和邀请码")
    if code.used_count >= code.max_uses:
        raise HTTPException(400, "无法注册，请确认邮箱和邀请码")

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
        raise HTTPException(400, "无法注册，请确认邮箱和邀请码")
    # 原子占用一次邀请码，避免并发注册超过 max_uses（ACC-04）。
    used = db.execute(
        update(InviteCode)
        .where(InviteCode.id == code.id, InviteCode.used_count < InviteCode.max_uses)
        .values(used_count=InviteCode.used_count + 1)
    )
    if used.rowcount != 1:
        db.rollback()
        raise HTTPException(400, "无法注册，请确认邮箱和邀请码")

    chat = _default_workspace(db, user)
    # 首个群聊也把 Ubuntu 环境预热起来（后台执行，不挡注册）
    from app.sandbox import linux as linux_sandbox

    background.add_task(linux_sandbox.warm_container, user.id, chat.id)
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
    ip = _ip(request)
    cutoff = now() - timedelta(minutes=15)
    for condition, by_ip in ((LoginAttempt.email == email, False), (LoginAttempt.ip == ip, True)):
        query = select(LoginAttempt).where(condition, LoginAttempt.created_at >= cutoff)
        if by_ip:
            query = query.where(LoginAttempt.email != _REGISTER_ATTEMPT_EMAIL)
        failures = db.scalars(query
                              .order_by(LoginAttempt.created_at.desc()).limit(5)).all()
        if len(failures) >= 5:
            retry = max(1, int((failures[0].created_at + timedelta(minutes=15) - now()).total_seconds()) + 1)
            log.warning("登录被限流 email=%s ip=%s 重试=%ds", email, ip, retry)
            raise HTTPException(429, "连续登录失败过多，请在 15 分钟后重试", headers={"Retry-After": str(retry)})
    user = db.scalar(select(User).where(User.email == email))
    valid_password = verify_password(payload.password, user.password_hash if user else _DUMMY_PASSWORD_HASH)
    if not user or not valid_password:
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
def logout(response: Response, user: User = Depends(current_user), db: Session = Depends(get_db)):
    user.session_epoch = (user.session_epoch or 0) + 1
    response.delete_cookie(SESSION_COOKIE, path="/")
    return {"ok": True}


# --------------------------------------------------------------------------- #
# 第三方登录（Google / GitHub）
# --------------------------------------------------------------------------- #
def _oauth_redirect_uri(provider: str, request: Request) -> str:
    """必须和 OAuth 后台登记的 Redirect URI 完全一致，所以允许显式配置。"""
    base = OAUTH_REDIRECT_BASE or _base_url(request)
    return f"{base}/api/auth/oauth/{provider}/callback"


def _oauth_fail(message: str, path: str = "/login") -> RedirectResponse:
    """错误统一送回登录页（绑定流程回设置页），顺手清掉 state Cookie。"""
    resp = RedirectResponse(f"{path}?oauth_error={quote(message)}", status_code=302)
    resp.delete_cookie(OAUTH_STATE_COOKIE, path="/api/auth/oauth")
    return resp


@router.get("/oauth/providers")
def oauth_providers():
    """前端据此决定显示哪几个登录按钮；没配 client id/secret 的一律隐藏。"""
    return {
        "google": oauth.enabled("google"),
        "github": oauth.enabled("github"),
        "signup": OAUTH_ALLOW_SIGNUP,
    }


@router.get("/oauth/accounts")
def oauth_accounts(
    request: Request, user: User = Depends(current_user), db: Session = Depends(get_db)
):
    """当前用户绑定了哪些第三方账号，以及回调地址（设置页照着填到对方后台）。"""
    rows = db.scalars(select(OAuthAccount).where(OAuthAccount.user_id == user.id)).all()
    return {
        "available": {"google": oauth.enabled("google"), "github": oauth.enabled("github")},
        "redirect_uri": {
            p: _oauth_redirect_uri(p, request) for p in ("google", "github")
        },
        "linked": {
            r.provider: {
                "name": r.name,
                "email": r.email,
                "avatar": r.avatar_url,
                "last_login_at": r.last_login_at.isoformat() if r.last_login_at else None,
            }
            for r in rows
        },
    }


@router.delete("/oauth/{provider}")
def oauth_unbind(
    provider: str,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    """解绑当前账号的某个第三方登录。"""
    if provider not in ("google", "github"):
        raise HTTPException(404, "不支持的登录方式")
    row = db.scalar(
        select(OAuthAccount).where(
            OAuthAccount.user_id == user.id, OAuthAccount.provider == provider
        )
    )
    if not row:
        raise HTTPException(404, "尚未绑定该账号")
    db.delete(row)
    db.flush()
    left = db.scalar(
        select(func.count())
        .select_from(OAuthAccount)
        .where(OAuthAccount.user_id == user.id)
    )
    log.info("OAuth 解绑 provider=%s uid=%s", provider, fingerprint(user.id))
    return {
        "ok": True,
        # 一个都没剩时前端提示「记得确认邮箱密码可用」
        "remaining": int(left or 0),
    }


def _oauth_start(
    provider: str, request: Request, bind_user_id: str | None = None
) -> RedirectResponse:
    if not oauth.enabled(provider):
        raise HTTPException(404, "该登录方式未启用")
    state, nonce = make_oauth_state(provider, bind_user_id)
    url = oauth.authorize_url(provider, state, _oauth_redirect_uri(provider, request))
    resp = RedirectResponse(url, status_code=302)
    # nonce 只留在浏览器里，回调时必须和 state 里的值对上，防止别人伪造跳转
    resp.set_cookie(
        OAUTH_STATE_COOKIE,
        nonce,
        max_age=OAUTH_STATE_MAX_AGE,
        httponly=True,
        samesite="lax",
        secure=COOKIE_SECURE or request.url.scheme == "https",
        path="/api/auth/oauth",
    )
    log.info(
        "OAuth 授权开始 provider=%s 绑定=%s ip=%s",
        provider, bool(bind_user_id), _ip(request),
    )
    return resp


@router.get("/oauth/{provider}/start")
def oauth_start(provider: str, request: Request):
    return _oauth_start(provider, request)


@router.get("/oauth/{provider}/bind/start")
def oauth_bind_start(
    provider: str, request: Request, user: User = Depends(current_user)
):
    """已登录用户把第三方账号绑到当前账号。"""
    return _oauth_start(provider, request, bind_user_id=user.id)


@router.get("/oauth/{provider}/callback")
async def oauth_callback(
    provider: str,
    request: Request,
    background: BackgroundTasks,
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
    db: Session = Depends(get_db),
):
    if error:
        log.info("OAuth 授权被拒绝 provider=%s error=%s", provider, error)
        return _oauth_fail("已取消授权")
    if not oauth.enabled(provider):
        return _oauth_fail("该登录方式未启用")
    checked = read_oauth_state(state, request.cookies.get(OAUTH_STATE_COOKIE))
    if not checked or checked["provider"] != provider:
        log.warning("OAuth state 校验失败 provider=%s ip=%s", provider, _ip(request))
        return _oauth_fail("登录状态已失效，请重新登录")
    if not code:
        return _oauth_fail("授权码缺失，请重新登录")

    try:
        profile = await oauth.fetch_profile(provider, code, _oauth_redirect_uri(provider, request))
    except oauth.OAuthError as exc:
        log.warning("OAuth 获取资料失败 provider=%s detail=%s", provider, exc.detail)
        return _oauth_fail(exc.message)
    if not profile.uid:
        return _oauth_fail("没拿到账号标识，请重新登录")

    # 0) 绑定流程：已登录用户在设置页点「绑定」，把账号挂到当前用户身上
    bind_user_id = checked["bind"]
    if bind_user_id:
        session = read_session(request.cookies.get(SESSION_COOKIE))
        if not session or session[0] != bind_user_id:
            return _oauth_fail("登录状态已变化，请重新登录后再绑定", "/settings")
        holder = db.scalar(
            select(OAuthAccount).where(
                OAuthAccount.provider == provider,
                OAuthAccount.provider_uid == profile.uid,
            )
        )
        if holder and holder.user_id != bind_user_id:
            return _oauth_fail("这个账号已经绑定到其他用户了", "/settings")
        if holder is None:
            holder = OAuthAccount(
                user_id=bind_user_id, provider=provider, provider_uid=profile.uid
            )
            db.add(holder)
        holder.email = profile.email or holder.email
        holder.name = profile.name or holder.name
        holder.avatar_url = profile.avatar or holder.avatar_url
        holder.last_login_at = now()
        db.flush()
        resp = RedirectResponse(f"/settings?oauth_bound={provider}", status_code=302)
        resp.delete_cookie(OAUTH_STATE_COOKIE, path="/api/auth/oauth")
        log.info("OAuth 绑定成功 provider=%s uid=%s", provider, fingerprint(bind_user_id))
        return resp

    # 1) 绑定过就直接登录
    account = db.scalar(
        select(OAuthAccount).where(
            OAuthAccount.provider == provider,
            OAuthAccount.provider_uid == profile.uid,
        )
    )
    user = db.get(User, account.user_id) if account else None
    if account and user is None:  # 账号被彻底删除，绑定信息也一起清掉
        db.delete(account)
        db.flush()
        account = None

    # 2) 没绑定：按邮箱认领已有账号，或者（允许时）直接建号
    created = False
    if user is None:
        if not profile.email_verified:
            return _oauth_fail("该账号的邮箱未验证，无法登录")
        user = (
            db.scalar(select(User).where(User.email == profile.email))
            if profile.email
            else None
        )
        if user is None:
            if not OAUTH_ALLOW_SIGNUP:
                return _oauth_fail("这个账号还没注册，请先用邀请码注册后再绑定")
            if not profile.email:
                return _oauth_fail("该账号没有可用邮箱，无法创建账号")
            user = User(
                email=profile.email,
                # 随机密码：这条路只走第三方登录，需要密码可去设置页另设
                password_hash=hash_password(random_token(32)),
                name=profile.name or profile.email.split("@")[0],
            )
            db.add(user)
            try:
                db.flush()
            except IntegrityError:
                db.rollback()
                return _oauth_fail("该邮箱已注册，请用原账号登录后再绑定")
            user.email_verified_at = now()
            chat = _default_workspace(db, user)
            from app.sandbox import linux as linux_sandbox

            background.add_task(linux_sandbox.warm_container, user.id, chat.id)
            created = True

    if user.deleted_at is not None or user.status != "active":
        log.warning("OAuth 登录被拒 uid=%s 状态=%s", fingerprint(user.id), user.status)
        return _oauth_fail("账号不可用")

    if account is None:
        account = OAuthAccount(
            user_id=user.id, provider=provider, provider_uid=profile.uid
        )
        db.add(account)
    account.email = profile.email or account.email
    account.name = profile.name or account.name
    account.avatar_url = profile.avatar or account.avatar_url
    account.last_login_at = now()
    db.flush()

    resp = RedirectResponse("/chat", status_code=302)
    _set_cookie(resp, user.id, request, user.session_epoch or 0)
    resp.delete_cookie(OAUTH_STATE_COOKIE, path="/api/auth/oauth")
    log.info(
        "OAuth 登录成功 provider=%s uid=%s 新建=%s ip=%s",
        provider, fingerprint(user.id), created, _ip(request),
    )
    return resp


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
    base = _base_url(request)
    _throttle_mail_ip(db, request, "reset_password")
    _throttle(db, email, "reset_password")
    user = db.scalar(select(User).where(User.email == email, User.status == "active"))

    link: str | None = None
    if user:
        raw = _issue_token(db, user, "reset_password", timedelta(minutes=RESET_TOKEN_TTL_MINUTES), request)
        link = f"{base}/reset-password?token={quote(raw)}"
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
    _throttle_mail_ip(db, request, "verify_email")
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
