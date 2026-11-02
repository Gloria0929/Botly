"""Google / GitHub 第三方登录。

不联网：授权地址只做字符串校验，回调里的「换 token + 拉资料」用假的
`oauth.fetch_profile` 替掉，重点验证 state 校验、绑定/建号/拒绝三条分支。
"""
from __future__ import annotations

from urllib.parse import parse_qs, urlparse

import pytest

from app.services import oauth
from app.api import auth as auth_api
from app.core.config import OAUTH_STATE_COOKIE
from app.services.oauth import Profile
from app.core.security import make_oauth_state, read_oauth_state
from tests.conftest import register

CALLBACK = "/api/auth/oauth/{p}/callback"


@pytest.fixture(autouse=True)
def _clean_credentials(monkeypatch):
    """测试不读本机 .env：凭据默认清空，要启用某个 provider 用 _enabled()。"""
    for name in (
        "GOOGLE_CLIENT_ID",
        "GOOGLE_CLIENT_SECRET",
        "GITHUB_CLIENT_ID",
        "GITHUB_CLIENT_SECRET",
    ):
        monkeypatch.setattr(oauth, name, "")
    monkeypatch.setattr(auth_api, "OAUTH_ALLOW_SIGNUP", True)


def _enabled(monkeypatch, provider: str = "google") -> None:
    monkeypatch.setattr(oauth, "enabled", lambda p: p == provider)


def _profile(monkeypatch, provider="google", uid="g-1", email="oauth@example.com", verified=True):
    async def fake_fetch(p, code, redirect_uri):
        assert p == provider
        return Profile(
            provider=p,
            uid=uid,
            email=email,
            email_verified=verified,
            name="OAuth User",
            avatar="https://example.com/a.png",
        )

    monkeypatch.setattr(oauth, "fetch_profile", fake_fetch)


def _state_cookie(client, provider="google") -> str:
    state, nonce = make_oauth_state(provider)
    client.cookies.set(OAUTH_STATE_COOKIE, nonce, path="/api/auth/oauth")
    return state


def test_state_roundtrip():
    state, nonce = make_oauth_state("github")
    assert read_oauth_state(state, nonce) == {"provider": "github", "bind": None}
    state2, nonce2 = make_oauth_state("google", "u-1")
    assert read_oauth_state(state2, nonce2) == {"provider": "google", "bind": "u-1"}
    assert read_oauth_state(state, "wrong") is None
    assert read_oauth_state("garbage", nonce) is None


def test_authorize_url_carries_state_and_scope(monkeypatch):
    monkeypatch.setattr(oauth, "GOOGLE_CLIENT_ID", "cid")
    monkeypatch.setattr(oauth, "GOOGLE_CLIENT_SECRET", "secret")
    url = oauth.authorize_url("google", "st-1", "https://x/api/auth/oauth/google/callback")
    parsed = urlparse(url)
    q = parse_qs(parsed.query)
    assert parsed.netloc == "accounts.google.com"
    assert q["client_id"] == ["cid"]
    assert q["state"] == ["st-1"]
    assert "email" in q["scope"][0]
    assert q["redirect_uri"] == ["https://x/api/auth/oauth/google/callback"]


def test_providers_hidden_without_credentials(client):
    r = client.get("/api/auth/oauth/providers")
    assert r.status_code == 200
    assert r.json() == {"google": False, "github": False, "signup": True}


def test_start_404_when_not_configured(client):
    r = client.get("/api/auth/oauth/google/start", follow_redirects=False)
    assert r.status_code == 404


def test_start_redirects_and_sets_state_cookie(client, monkeypatch):
    _enabled(monkeypatch)
    monkeypatch.setattr(
        oauth, "authorize_url", lambda p, state, uri: f"https://accounts.google.com/auth?state={state}"
    )
    r = client.get("/api/auth/oauth/google/start", follow_redirects=False)
    assert r.status_code == 302
    assert r.headers["location"].startswith("https://accounts.google.com/auth?state=")
    assert client.cookies.get(OAUTH_STATE_COOKIE)


def test_callback_rejects_forged_state(client, monkeypatch):
    _enabled(monkeypatch)
    client.cookies.clear()
    r = client.get(
        CALLBACK.format(p="google") + "?code=abc&state=forged",
        follow_redirects=False,
    )
    assert r.status_code == 302
    assert "/login?oauth_error=" in r.headers["location"]
    assert "state" not in r.headers["location"]  # 不把技术细节带给前端


def test_callback_links_existing_email(client, invite, monkeypatch):
    email = "oauth-link@example.com"
    client.cookies.clear()
    register(client, invite, email)
    client.post("/api/auth/logout")
    client.cookies.clear()

    _enabled(monkeypatch)
    _profile(monkeypatch, email=email)
    state = _state_cookie(client)

    r = client.get(CALLBACK.format(p="google") + f"?code=abc&state={state}", follow_redirects=False)
    assert r.status_code == 302 and r.headers["location"] == "/chat"
    me = client.get("/api/auth/me").json()
    assert me["email"] == email


def test_callback_creates_account_when_signup_allowed(client, monkeypatch):
    email = "oauth-new@example.com"
    client.cookies.clear()
    _enabled(monkeypatch)
    monkeypatch.setattr(auth_api, "OAUTH_ALLOW_SIGNUP", True)
    _profile(monkeypatch, uid="g-new", email=email)
    state = _state_cookie(client)

    r = client.get(CALLBACK.format(p="google") + f"?code=abc&state={state}", follow_redirects=False)
    assert r.status_code == 302 and r.headers["location"] == "/chat"
    me = client.get("/api/auth/me").json()
    assert me["email"] == email
    assert me["email_verified"] is True
    # 新账号要有默认工作区，否则登录后是空壳
    chats = client.get("/api/chats").json()
    assert len(chats) == 1


def test_callback_blocks_unknown_account_when_signup_disabled(client, monkeypatch):
    client.cookies.clear()
    _enabled(monkeypatch)
    monkeypatch.setattr(auth_api, "OAUTH_ALLOW_SIGNUP", False)
    _profile(monkeypatch, uid="g-x", email="nobody@example.com")
    state = _state_cookie(client)

    r = client.get(CALLBACK.format(p="google") + f"?code=abc&state={state}", follow_redirects=False)
    assert r.status_code == 302
    assert "oauth_error" in r.headers["location"]
    assert client.get("/api/auth/me").status_code == 401


def test_callback_rejects_unverified_email(client, monkeypatch):
    client.cookies.clear()
    _enabled(monkeypatch)
    _profile(monkeypatch, uid="g-v", email="unverified@example.com", verified=False)
    state = _state_cookie(client)

    r = client.get(CALLBACK.format(p="google") + f"?code=abc&state={state}", follow_redirects=False)
    assert r.status_code == 302
    assert "oauth_error" in r.headers["location"]


def test_bind_links_provider_even_with_other_email(client, invite, monkeypatch):
    """已登录用户绑定：对方邮箱和本站邮箱不同也要能绑上。"""
    client.cookies.clear()
    register(client, invite, "bind@example.com")
    uid = client.get("/api/auth/me").json()["id"]

    _enabled(monkeypatch)
    _profile(monkeypatch, uid="g-bind", email="somewhere-else@example.com")
    state, nonce = make_oauth_state("google", uid)
    client.cookies.set(OAUTH_STATE_COOKIE, nonce, path="/api/auth/oauth")

    r = client.get(CALLBACK.format(p="google") + f"?code=abc&state={state}", follow_redirects=False)
    assert r.status_code == 302
    assert r.headers["location"] == "/settings?oauth_bound=google"

    accounts = client.get("/api/auth/oauth/accounts").json()
    assert "google" in accounts["linked"]
    assert accounts["linked"]["google"]["email"] == "somewhere-else@example.com"


def test_bind_refuses_account_owned_by_someone_else(client, invite, monkeypatch):
    client.cookies.clear()
    # 先用 A 账号把 g-taken 绑走
    register(client, invite, "owner@example.com")
    _enabled(monkeypatch)
    _profile(monkeypatch, uid="g-taken", email="owner@example.com")
    state = _state_cookie(client)
    client.get(CALLBACK.format(p="google") + f"?code=abc&state={state}", follow_redirects=False)
    client.post("/api/auth/logout")
    client.cookies.clear()

    # B 账号再想绑同一个 Google 账号
    register(client, invite, "thief@example.com")
    uid = client.get("/api/auth/me").json()["id"]
    _profile(monkeypatch, uid="g-taken", email="owner@example.com")
    state2, nonce2 = make_oauth_state("google", uid)
    client.cookies.set(OAUTH_STATE_COOKIE, nonce2, path="/api/auth/oauth")
    r = client.get(CALLBACK.format(p="google") + f"?code=abc&state={state2}", follow_redirects=False)
    assert r.status_code == 302
    assert "oauth_error" in r.headers["location"]
    assert "google" not in client.get("/api/auth/oauth/accounts").json()["linked"]
