"""Google / GitHub 第三方登录：只负责「code 换 token、拉用户资料」。

会话、绑定关系与建号都在 `api/auth.py` 里做，这里保持无数据库依赖，方便单测。
"""
from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlencode

import httpx

from app.core.config import (
    GITHUB_CLIENT_ID,
    GITHUB_CLIENT_SECRET,
    GOOGLE_CLIENT_ID,
    GOOGLE_CLIENT_SECRET,
    OAUTH_TIMEOUT,
)
from app.core.logging_setup import get_logger

log = get_logger("oauth")


class OAuthError(Exception):
    """message 会直接跳回登录页展示，不要塞技术细节；detail 只进日志。"""

    def __init__(self, message: str, detail: str = ""):
        super().__init__(message)
        self.message = message
        self.detail = detail


@dataclass(frozen=True)
class Profile:
    provider: str
    uid: str
    email: str | None
    email_verified: bool
    name: str | None
    avatar: str | None


_PROVIDERS = {
    "google": {
        "authorize": "https://accounts.google.com/o/oauth2/v2/auth",
        "token": "https://oauth2.googleapis.com/token",
        "scope": "openid email profile",
        # 每次都让用户选账号，避免共用设备时静默登进上一个账号
        "extra": {"access_type": "online", "prompt": "select_account"},
    },
    "github": {
        "authorize": "https://github.com/login/oauth/authorize",
        "token": "https://github.com/login/oauth/access_token",
        "scope": "read:user user:email",
        "extra": {},
    },
}

SUPPORTED = tuple(_PROVIDERS)


def credentials(provider: str) -> tuple[str, str] | None:
    """返回 (client_id, client_secret)；没配全就当没启用。"""
    if provider == "google" and GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET:
        return GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET
    if provider == "github" and GITHUB_CLIENT_ID and GITHUB_CLIENT_SECRET:
        return GITHUB_CLIENT_ID, GITHUB_CLIENT_SECRET
    return None


def enabled(provider: str) -> bool:
    return provider in _PROVIDERS and credentials(provider) is not None


def authorize_url(provider: str, state: str, redirect_uri: str) -> str:
    creds = credentials(provider)
    if not creds:
        raise OAuthError("该登录方式未启用")
    spec = _PROVIDERS[provider]
    params = {
        "client_id": creds[0],
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": spec["scope"],
        "state": state,
        **spec["extra"],
    }
    return f"{spec['authorize']}?{urlencode(params)}"


def _token_error(provider: str, payload: dict) -> OAuthError:
    code = str(payload.get("error") or "")
    desc = str(payload.get("error_description") or "")
    log.warning("OAuth 换 token 失败 provider=%s error=%s desc=%s", provider, code, desc)
    if code in ("invalid_grant", "bad_verification_code", "code_expired"):
        return OAuthError("授权已过期，请重新登录", f"{code}: {desc}")
    if code in ("invalid_client", "incorrect_client_credentials", "unauthorized_client"):
        return OAuthError("登录配置有误，请联系管理员", f"{code}: {desc}")
    if code in ("redirect_uri_mismatch",):
        return OAuthError("回调地址不匹配，请联系管理员", f"{code}: {desc}")
    return OAuthError("登录失败，请重试", f"{code}: {desc}")


async def _exchange_code(
    client: httpx.AsyncClient, provider: str, code: str, redirect_uri: str
) -> str:
    creds = credentials(provider)
    if not creds:
        raise OAuthError("该登录方式未启用")
    data = {
        "client_id": creds[0],
        "client_secret": creds[1],
        "code": code,
        "redirect_uri": redirect_uri,
        "grant_type": "authorization_code",
    }
    try:
        resp = await client.post(
            _PROVIDERS[provider]["token"],
            data=data,
            headers={"Accept": "application/json"},
        )
        payload = resp.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise OAuthError("连接登录服务失败，请稍后重试", str(exc))
    token = payload.get("access_token")
    if not token:
        raise _token_error(provider, payload if isinstance(payload, dict) else {})
    return str(token)


async def _google_profile(client: httpx.AsyncClient, token: str) -> Profile:
    resp = await client.get(
        "https://openidconnect.googleapis.com/v1/userinfo",
        headers={"Authorization": f"Bearer {token}"},
    )
    if resp.status_code != 200:
        raise OAuthError("读取 Google 账号资料失败", f"HTTP {resp.status_code}")
    data = resp.json()
    email = (data.get("email") or "").strip().lower() or None
    if not email:
        raise OAuthError("该 Google 账号没有可用邮箱，无法登录")
    return Profile(
        provider="google",
        uid=str(data.get("sub") or ""),
        email=email,
        email_verified=bool(data.get("email_verified")),
        name=(data.get("name") or email.split("@")[0])[:80],
        avatar=data.get("picture"),
    )


async def _github_profile(client: httpx.AsyncClient, token: str) -> Profile:
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        # GitHub 要求带 User-Agent，否则 403
        "User-Agent": "Botly",
    }
    resp = await client.get("https://api.github.com/user", headers=headers)
    if resp.status_code != 200:
        raise OAuthError("读取 GitHub 账号资料失败", f"HTTP {resp.status_code}")
    data = resp.json()
    email = (data.get("email") or "").strip().lower() or None
    verified = False
    # /user 里的 email 可能是空（用户设为私有），也可能不是主邮箱；
    # 邮箱绑定/建号只认 /user/emails 里 verified 的那条
    emails = await client.get("https://api.github.com/user/emails", headers=headers)
    if emails.status_code == 200:
        rows = [
            row for row in emails.json()
            if isinstance(row, dict) and row.get("verified") and row.get("email")
        ]
        rows.sort(key=lambda r: not r.get("primary"))
        if rows:
            email = str(rows[0]["email"]).strip().lower()
            verified = True
    login = data.get("login") or ""
    return Profile(
        provider="github",
        uid=str(data.get("id") or ""),
        email=email,
        email_verified=verified,
        name=(str(data.get("name") or login)[:80] or None),
        avatar=data.get("avatar_url"),
    )


async def fetch_profile(provider: str, code: str, redirect_uri: str) -> Profile:
    if provider not in _PROVIDERS:
        raise OAuthError("不支持的登录方式")
    async with httpx.AsyncClient(timeout=OAUTH_TIMEOUT, follow_redirects=True) as client:
        token = await _exchange_code(client, provider, code, redirect_uri)
        try:
            if provider == "google":
                return await _google_profile(client, token)
            return await _github_profile(client, token)
        except httpx.HTTPError as exc:
            raise OAuthError("读取账号资料失败，请稍后重试", str(exc))
