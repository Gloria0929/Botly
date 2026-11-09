"""2026-11-06 审查：撤销会话、提交失败，以及 Push / MCP 的实际出站目标。"""
import asyncio
import base64
import os
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from sqlalchemy import event, select
from sqlalchemy.orm import Session

from app.core.config import OAUTH_STATE_COOKIE, SESSION_COOKIE
from app.core.db import SessionLocal
from app.core.models import OAuthAccount, User, now
from app.services import oauth, push
from app.tools import mcp, registry
from tests.conftest import register


@pytest.fixture(autouse=True)
def _isolated_cookies(client):
    client.cookies.clear()
    yield
    client.cookies.clear()


@pytest.mark.parametrize("revocation", ["password", "logout", "disabled", "deleted"])
def test_revoked_session_cannot_complete_oauth_binding(client, invite, monkeypatch, revocation):
    account = register(client, invite, f"audit-oauth-{revocation}@test.dev")
    cookie = client.cookies.get(SESSION_COOKIE)
    monkeypatch.setattr(oauth, "enabled", lambda provider: provider == "google")
    monkeypatch.setattr(oauth, "authorize_url", lambda p, state, uri: f"https://example.invalid/?state={state}")

    async def profile(*args):
        return oauth.Profile("google", f"audit-{revocation}", "external@test.dev", True, "External", None)

    monkeypatch.setattr(oauth, "fetch_profile", profile)
    started = client.get("/api/auth/oauth/google/bind/start", follow_redirects=False)
    state = parse_qs(urlsplit(started.headers["location"]).query)["state"][0]
    nonce = client.cookies.get(OAUTH_STATE_COOKIE)
    if revocation == "password":
        assert client.post("/api/auth/password", json={"current_password": "password-123", "new_password": "new-password-456"}).status_code == 200
    elif revocation == "logout":
        assert client.post("/api/auth/logout").status_code == 200
    else:
        with SessionLocal() as db:
            user = db.get(User, account["id"])
            if revocation == "disabled":
                user.status = "disabled"
            else:
                user.deleted_at = now()
            db.commit()
    client.cookies.clear()
    client.cookies.set(SESSION_COOKIE, cookie)
    client.cookies.set(OAUTH_STATE_COOKIE, nonce, path="/api/auth/oauth")
    response = client.get("/api/auth/oauth/google/callback", params={"code": "mock", "state": state}, follow_redirects=False)
    assert "oauth_error=" in response.headers["location"]
    with SessionLocal() as db:
        assert db.scalar(select(OAuthAccount.id).where(OAuthAccount.user_id == account["id"])) is None


def test_commit_failure_returns_500_and_rolls_back(client, invite, monkeypatch):
    register(client, invite, "audit-commit@test.dev")
    bot = client.get("/api/bots").json()[0]
    armed = [True]

    def fail_once(db):
        if armed[0]:
            armed[0] = False
            raise RuntimeError("simulated commit failure")

    monkeypatch.setattr(client._transport, "raise_server_exceptions", False)
    event.listen(Session, "before_commit", fail_once)
    try:
        response = client.patch(f"/api/bots/{bot['id']}", json={"name": "must-not-be-saved"})
    finally:
        event.remove(Session, "before_commit", fail_once)
    assert not armed[0] and response.status_code == 500
    assert client.get(f"/api/bots/{bot['id']}").json()["name"] == bot["name"]


@pytest.mark.parametrize("endpoint", [
    "http://localhost:8100/internal", "http://localhost.example/x", "https://127.0.0.1/x",
    "https://10.0.0.1/x", "https://169.254.169.254/x", "https://[::1]/x",
    "https://user:secret@8.8.8.8/x", "https://8.8.8.8:0/x", "https://8.8.8.8/x#fragment",
])
def test_push_subscription_rejects_unsafe_endpoints(client, invite, endpoint):
    register(client, invite, f"audit-push-{abs(hash(endpoint))}@test.dev")
    response = client.post("/api/push/subscriptions", json={"endpoint": endpoint, "keys": {"p256dh": "key", "auth": "auth"}})
    assert response.status_code == 422


def test_push_revalidates_dns_before_delivery(monkeypatch):
    addresses = iter(("93.184.216.34", "10.0.0.1"))
    monkeypatch.setattr(registry.socket, "getaddrinfo", lambda *a, **kw: [(2, 1, 6, "", (next(addresses), 0))])
    endpoint = "https://push.example.test/send"
    assert push.validate_endpoint(endpoint) == "93.184.216.34"
    sent = []
    with httpx.Client(transport=httpx.MockTransport(lambda request: sent.append(request))) as client:
        with pytest.raises(ValueError, match="内网"):
            push._PushSession(client).post(endpoint, data=b"encrypted", headers={}, timeout=1)
    assert not sent


@pytest.mark.parametrize("status", [201, 302, 410])
def test_encrypted_push_pins_ip_and_preserves_tls_host_without_redirects(monkeypatch, status):
    from pywebpush import WebPushException

    encode = lambda data: base64.urlsafe_b64encode(data).rstrip(b"=").decode()
    private = ec.generate_private_key(ec.SECP256R1())
    public = private.public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
    subscription = {"endpoint": "https://push.example.test/v1/send", "p256dh": encode(public), "auth": encode(os.urandom(16))}
    monkeypatch.setattr(push, "_check_url", lambda url: "93.184.216.34")
    requests, options = [], []
    real_client = httpx.Client

    def handler(request):
        requests.append(request)
        return httpx.Response(status, headers={"Location": "http://127.0.0.1/private"}, text="response")

    def client(**kwargs):
        options.append(kwargs)
        return real_client(transport=httpx.MockTransport(handler), **kwargs)

    monkeypatch.setattr(push.httpx, "Client", client)
    if status == 201:
        push.send_to_subscription(subscription, '{"message":"test"}')
    else:
        with pytest.raises(WebPushException) as failure:
            push.send_to_subscription(subscription, '{"message":"test"}')
        assert failure.value.response.status_code == status
    assert len(requests) == 1 and requests[0].url.host == "93.184.216.34"
    assert requests[0].headers["host"] == "push.example.test"
    assert requests[0].extensions["sni_hostname"] == "push.example.test"
    assert requests[0].content and b'"message":"test"' not in requests[0].content
    assert options[0]["trust_env"] is False and options[0]["follow_redirects"] is False


@pytest.mark.parametrize("status", [200, 302])
def test_mcp_pins_checked_ip_and_rejects_redirects(monkeypatch, status):
    requests, options = [], []
    monkeypatch.setattr(mcp, "_check_url", lambda url: "93.184.216.34")
    real_client = httpx.AsyncClient

    def handler(request):
        requests.append(request)
        return httpx.Response(status, json={"result": {}}, headers={"Location": "http://127.0.0.1/private"})

    def client(**kwargs):
        options.append(kwargs)
        return real_client(transport=httpx.MockTransport(handler), **kwargs)

    monkeypatch.setattr(mcp.httpx, "AsyncClient", client)
    call = mcp._post("https://mcp.example.test/rpc", {"id": 1}, "test-token")
    if status == 200:
        asyncio.run(call)
    else:
        with pytest.raises(mcp.McpError, match="重定向"):
            asyncio.run(call)
    assert len(requests) == 1 and requests[0].url.host == "93.184.216.34"
    assert requests[0].headers["host"] == "mcp.example.test"
    assert requests[0].extensions["sni_hostname"] == "mcp.example.test"
    assert options[0]["trust_env"] is False and options[0]["follow_redirects"] is False
