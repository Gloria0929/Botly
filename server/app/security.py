"""密码哈希、API Key 对称加密、会话 Cookie。"""
from __future__ import annotations

import base64
import hashlib
import hmac
import os

import bcrypt
from cryptography.fernet import Fernet, InvalidToken
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

from .config import ENCRYPTION_KEY, SECRET_KEY, SESSION_MAX_AGE

_serializer = URLSafeTimedSerializer(SECRET_KEY, salt="botly-session")
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
def make_session(user_id: str) -> str:
    return _serializer.dumps({"uid": user_id})


def read_session(token: str | None) -> str | None:
    if not token:
        return None
    try:
        data = _serializer.loads(token, max_age=SESSION_MAX_AGE)
    except (BadSignature, SignatureExpired):
        return None
    return data.get("uid")


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
