"""审查文档 S1–S4：SSRF、stdio 权限、代理身份与认证链接。"""
import asyncio
import sys

import httpx
import pytest
from starlette.requests import Request

from app.agent import llm
from app.api import auth, mcp as api_mcp
from app.core import deps
from app.core.db import SessionLocal
from app.core.models import McpServer
from app.tools import mcp
from tests.conftest import register


@pytest.mark.parametrize("url", [
    "http://127.0.0.1/v1", "http://redis:6379", "http://169.254.169.254",
    "http://[::1]", "http://100.64.0.1", "file:///etc/passwd", "http://8.8.8.8:bad",
    "https://user:password@8.8.8.8", "https://8.8.8.8/v1?override=1",
])
def test_provider_and_runtime_reject_unsafe_urls(client, invite, monkeypatch, url):
    register(client, invite, f"ssrf-{abs(hash(url))}@test.dev")
    called = []
    monkeypatch.setattr("app.tools.registry.socket.getaddrinfo",
                        lambda *args, **kw: [(2, 1, 6, "", ("10.0.0.1", 0))])

    async def probe(**kw):
        called.append(kw)
        return True, ["model"], "ok"

    monkeypatch.setattr("app.api.providers.list_models", probe)
    response = client.post("/api/providers", json={"vendor_id": "custom", "base_url": url})
    assert response.status_code == 400 and not called
    with pytest.raises(llm.LLMError):
        asyncio.run(llm._guard_base_url(url))


@pytest.mark.parametrize("action", ["refresh", "test", "probe", "patch"])
def test_legacy_provider_cannot_bypass_guard(client, invite, action):
    register(client, invite, f"ssrf-legacy-{action}@test.dev")
    created = client.post("/api/providers", json={"kind": "mock"}).json()["provider"]
    from app.core.models import Provider

    with SessionLocal() as db:
        provider = db.get(Provider, created["id"])
        provider.kind, provider.base_url = "openai", "http://169.254.169.254"
        db.commit()
    url = f"/api/providers/{created['id']}"
    response = client.patch(url, json={"api_key": "new-key"}) if action == "patch" else client.post(f"{url}/{action}")
    assert response.status_code == 400


def test_private_model_opt_in_still_validates_url(monkeypatch):
    monkeypatch.setattr(llm, "ALLOW_PRIVATE_BASE_URL", True)
    assert asyncio.run(llm._guard_base_url("http://127.0.0.1:11434/v1")) is None
    for url in ("file:///etc/passwd", "http://8.8.8.8:bad", "http://user:pass@localhost"):
        with pytest.raises(llm.LLMError):
            asyncio.run(llm._guard_base_url(url))


@pytest.mark.parametrize("action", ["models", "capabilities", "stream"])
def test_model_connection_is_pinned_and_error_body_is_hidden(monkeypatch, action):
    requests, options = [], []
    real_client = httpx.AsyncClient
    monkeypatch.setattr(llm, "_check_url", lambda url: "93.184.216.34")
    monkeypatch.setattr(llm, "ALLOW_PRIVATE_BASE_URL", False)
    monkeypatch.setattr(llm, "LLM_MAX_RETRIES", 0)

    def handle(request):
        requests.append(request)
        return httpx.Response(400, text="INTERNAL_SECRET backend metadata")

    def client(**kw):
        options.append(kw)
        return real_client(transport=httpx.MockTransport(handle), **kw)

    monkeypatch.setattr(llm.httpx, "AsyncClient", client)

    async def check():
        args = {"base_url": "https://public.example/v1", "api_key": "secret-key"}
        if action == "models":
            return str(await llm.list_models(**args))
        if action == "capabilities":
            return str(await llm.probe_capabilities(**args, model="model"))
        try:
            async for _ in llm.stream_chat(**args, kind="openai", model="model", messages=[]):
                pass
        except llm.LLMError as exc:
            return str(exc)

    result = asyncio.run(check())
    assert "INTERNAL_SECRET" not in result and "400" in result
    assert requests and all(r.url.host == "93.184.216.34" for r in requests)
    assert all(r.headers["Host"] == "public.example" for r in requests)
    assert all(r.extensions["sni_hostname"] == "public.example" for r in requests)
    assert all(o["trust_env"] is False and o["follow_redirects"] is False for o in options)


def _stdio_enabled(monkeypatch):
    admins = {"stdio-admin@test.dev"}
    for module in (mcp, api_mcp):
        monkeypatch.setattr(module, "MCP_STDIO_ENABLED", True)
        monkeypatch.setattr(module, "ADMIN_EMAILS", admins)
    monkeypatch.setattr(mcp, "MCP_STDIO_ALLOWED_COMMANDS", [[sys.executable, "/opt/mcp/server.py"]])


def test_stdio_disabled_even_for_existing_command(monkeypatch):
    monkeypatch.setattr(mcp, "MCP_STDIO_ENABLED", False)
    with pytest.raises(mcp.McpError, match="禁用"):
        asyncio.run(mcp._spawn_stdio(sys.executable, [], {}))


def test_stdio_rejects_unlisted_commands_and_argument_changes(monkeypatch):
    _stdio_enabled(monkeypatch)
    for command, args in (("python", []), ("/bin/sh", ["-c", "id"]),
                          (sys.executable, ["-c", "print(1)"]),
                          (sys.executable, ["/opt/mcp/server.py", "$(id)"])):
        with pytest.raises(mcp.McpError):
            mcp.validate_stdio(command, args)


def test_stdio_child_has_only_clean_environment(monkeypatch):
    _stdio_enabled(monkeypatch)
    monkeypatch.setenv("BOTLY_SMTP_PASSWORD", "must-not-inherit")
    monkeypatch.setenv("DATABASE_URL", "must-not-inherit")
    captured = {}

    async def spawn(*argv, **kw):
        captured.update(kw)
        return object()

    monkeypatch.setattr(mcp.asyncio, "create_subprocess_exec", spawn)
    asyncio.run(mcp._spawn_stdio(sys.executable, ["/opt/mcp/server.py"], {"MCP_TOKEN": "configured"}))
    assert set(captured["env"]) == {"PATH", "HOME", "MCP_TOKEN"}


def test_non_admin_legacy_stdio_is_blocked_at_test_and_runtime(client, invite, monkeypatch):
    _stdio_enabled(monkeypatch)
    account = register(client, invite, "stdio-non-admin@test.dev")
    with SessionLocal() as db:
        server = McpServer(user_id=account["id"], name="legacy", slug="legacy", transport="stdio",
                           url="", command=sys.executable, args=["/opt/mcp/server.py"], enabled=True)
        db.add(server)
        db.commit()
    assert client.post(f"/api/mcp/servers/{server.id}/test").status_code == 403
    assert client.post("/api/mcp/servers", json={"name": "new", "transport": "stdio",
                       "command": sys.executable, "args": ["/opt/mcp/server.py"]}).status_code == 403
    response = client.post("/api/mcp/import", json={"mcpServers": {"imported": {
        "command": sys.executable, "args": ["/opt/mcp/server.py"]}}}).json()
    assert not response["created"] and response["errors"]
    with pytest.raises(mcp.McpError, match="管理员"):
        asyncio.run(mcp.list_remote_tools(server, None))
    with pytest.raises(mcp.McpError, match="管理员"):
        asyncio.run(mcp.call_remote_tool(server, None, "tool", {}))


def test_spoofed_xff_cannot_bypass_login_limit(client):
    for i in range(5):
        response = client.post("/api/auth/login", headers={"X-Forwarded-For": f"1.1.1.{i}"},
                               json={"email": f"missing-{i}@test.dev", "password": "wrong"})
        assert response.status_code == 401
    assert client.post("/api/auth/login", headers={"X-Forwarded-For": "8.8.8.8"},
                       json={"email": "another@test.dev", "password": "wrong"}).status_code == 429


def test_spoofed_xff_cannot_bypass_registration_or_reset_limit(client):
    for i in range(10):
        response = client.post("/api/auth/register", headers={"X-Forwarded-For": f"1.1.1.{i}"},
            json={"email": f"register-{i}@test.dev", "password": "password-123", "invite_code": "invalid"})
        assert response.status_code == 400
    assert client.post("/api/auth/register", headers={"X-Forwarded-For": "8.8.8.8"},
        json={"email": "register-more@test.dev", "password": "password-123", "invite_code": "invalid"}).status_code == 429
    for i in range(20):
        assert client.post("/api/auth/forgot-password", headers={"X-Forwarded-For": f"1.1.1.{i}"},
                           json={"email": f"reset-{i}@test.dev"}).status_code == 200
    assert client.post("/api/auth/forgot-password", headers={"X-Forwarded-For": "8.8.8.8"},
                       json={"email": "reset-more@test.dev"}).status_code == 429


def test_client_ip_uses_verified_scope():
    request = Request({"type": "http", "client": ("203.0.113.2", 1234),
                       "headers": [(b"x-forwarded-for", b"attacker")], "method": "GET", "path": "/"})
    assert deps.client_ip(request) == "203.0.113.2"


@pytest.mark.parametrize("environment", ["production", "staging"])
def test_non_development_mail_rejects_host_derived_url(client, monkeypatch, environment):
    monkeypatch.setattr(auth, "APP_ENV", environment)
    monkeypatch.setattr(auth, "PUBLIC_BASE_URL", "")
    response = client.post("/api/auth/forgot-password", json={"email": "victim@test.dev"},
                           headers={"Host": "attacker.example", "X-Forwarded-Host": "attacker.example"})
    assert response.status_code == 503 and "dev_link" not in response.json()
    assert auth._dev_link("https://attacker.example/?token=secret") is None


def test_reset_link_uses_configured_origin(client, invite, monkeypatch):
    register(client, invite, "host-poison-victim@test.dev")
    monkeypatch.setattr(auth, "APP_ENV", "production")
    monkeypatch.setattr(auth, "PUBLIC_BASE_URL", "https://botly.example")
    sent = []
    monkeypatch.setattr(auth, "_send", lambda *args: sent.append(args))
    response = client.post("/api/auth/forgot-password", json={"email": "host-poison-victim@test.dev"},
                           headers={"Host": "attacker.example", "X-Forwarded-Host": "attacker.example"})
    assert response.status_code == 200 and "dev_link" not in response.json()
    assert "https://botly.example/reset-password" in sent[0][2]
    assert "attacker.example" not in sent[0][2]
