"""密码哈希、API Key 对称加密、会话 Cookie。"""
from __future__ import annotations

import base64
import hashlib
import hmac
import os

import bcrypt
from cryptography.fernet import Fernet, InvalidToken
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

from app.core.config import ENCRYPTION_KEY, OAUTH_STATE_MAX_AGE, SECRET_KEY, SESSION_MAX_AGE

_serializer = URLSafeTimedSerializer(SECRET_KEY, salt="botly-session")
_oauth_serializer = URLSafeTimedSerializer(SECRET_KEY, salt="botly-oauth-state")
_fernet = Fernet(ENCRYPTION_KEY)


# ---------------- 密码 ----------------
def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt(rounds=12)).decode()


def verify_password(password: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode("utf-8"), hashed.encode("utf-8"))
    except (ValueError, TypeError):
        return False


# ---------------- API Key 加解密 ----------------
def encrypt_secret(plain: str | None) -> str | None:
    if not plain:
        return None
    return _fernet.encrypt(plain.encode("utf-8")).decode("utf-8")


def decrypt_secret(token: str | None) -> str | None:
    if not token:
        return None
    try:
        return _fernet.decrypt(token.encode("utf-8")).decode("utf-8")
    except (InvalidToken, ValueError):
        return None


def mask_key(key: str | None) -> str:
    if not key:
        return ""
    return f"****{key[-4:]}" if len(key) > 4 else "****"


# ---------------- 会话 ----------------
def make_session(user_id: str, epoch: int = 0) -> str:
    """会话 Cookie：uid + 会话代数。

    epoch 与 users.session_epoch 比对，密码被重置后旧 Cookie 立即失效（ACC-08），
    不需要引入服务端会话表。
    """
    return _serializer.dumps({"uid": user_id, "ep": int(epoch)})


def read_session(token: str | None) -> tuple[str, int] | None:
    """返回 (user_id, epoch)；签名不对或过期返回 None。"""
    if not token:
        return None
    try:
        data = _serializer.loads(token, max_age=SESSION_MAX_AGE)
    except (BadSignature, SignatureExpired):
        return None
    uid = data.get("uid")
    if not uid:
        return None
    return uid, int(data.get("ep") or 0)


# ---------------- OAuth state ----------------
def make_oauth_state(provider: str, bind_user_id: str | None = None) -> tuple[str, str]:
    """生成 (state, nonce)：state 给对方的跳转地址，nonce 种在浏览器 Cookie 里。

    两者必须成对出现才能通过校验，防止别人伪造一个 state 把用户引到别人的账号上。
    bind_user_id 非空表示这是「已登录用户绑定第三方账号」的流程。
    """
    nonce = random_token(16)
    payload = {"p": provider, "n": nonce, "b": bind_user_id or None}
    return _oauth_serializer.dumps(payload), nonce


def read_oauth_state(state: str | None, nonce: str | None) -> dict | None:
    """校验 state，返回 {"provider":…, "bind":…}；签名不对、过期、与 Cookie 不匹配返回 None。"""
    if not state or not nonce:
        return None
    try:
        data = _oauth_serializer.loads(state, max_age=OAUTH_STATE_MAX_AGE)
    except (BadSignature, SignatureExpired):
        return None
    if not constant_time_eq(str(data.get("n") or ""), nonce):
        return None
    provider = str(data.get("p") or "")
    if not provider:
        return None
    bind = data.get("b")
    return {"provider": provider, "bind": str(bind) if bind else None}


# ---------------- 杂项 ----------------
def args_hash(payload: dict) -> str:
    """审批参数指纹：恢复执行时用于校验参数未被篡改（APV-05）。"""
    import json

    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def constant_time_eq(a: str, b: str) -> bool:
    return hmac.compare_digest(a, b)


def random_token(n: int = 24) -> str:
    return base64.urlsafe_b64encode(os.urandom(n)).decode().rstrip("=")


def token_hash(raw: str) -> str:
    """邮件里的一次性令牌只存摘要（ACC-08）。"""
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()
