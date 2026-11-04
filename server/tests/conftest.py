"""测试环境：独立临时数据库 + 不可达的 Redis（走降级路径）。

必须在导入 app 之前设置环境变量，因为 engine 在模块导入时创建。
"""
from __future__ import annotations

import os
import tempfile

_TMP = tempfile.mkdtemp(prefix="botly-test-")
os.environ["BOTLY_DATA_DIR"] = _TMP
os.environ["DATABASE_URL"] = f"sqlite:///{_TMP}/test.db"
# 指向一个没有服务的端口：连接被立即拒绝，测试不依赖 Redis
os.environ["BOTLY_REDIS_URL"] = "redis://127.0.0.1:6399/0"
os.environ["BOTLY_SECRET_KEY"] = "test-secret-key"
os.environ["BOTLY_ENV"] = "development"
# ACC-08：测试不发真实邮件，走「写日志 + 返回 dev 链接」的开发模式
os.environ["BOTLY_SMTP_HOST"] = ""
os.environ["BOTLY_MAIL_DEV"] = "1"
os.environ["BOTLY_SANDBOX_PROVIDER"] = "docker"

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.core.db import SessionLocal  # noqa: E402
from app.main import app  # noqa: E402
from app.core.models import InviteCode  # noqa: E402


@pytest.fixture(autouse=True)
def _isolate_auth_rate_limits(client):
    """共享测试库的不同用例不共用登录/邮件限流计数；用例内仍使用真实限流。"""
    from sqlalchemy import delete
    from app.core.models import EmailToken, LoginAttempt

    with SessionLocal() as db:
        db.execute(delete(LoginAttempt))
        db.execute(delete(EmailToken))
        db.commit()


@pytest.fixture(scope="session")
def client():
    with TestClient(app, headers={"X-Botly-Client": "web"}) as c:
        yield c


@pytest.fixture(autouse=True)
def _no_container_warm(monkeypatch):
    """建群聊会在后台预热 Docker 容器；测试里不碰真实 Docker。"""
    from app.sandbox import linux as linux_sandbox

    async def noop(user_id: str, chat_id: str) -> None:
        return None

    monkeypatch.setattr(linux_sandbox, "warm_container", noop)


@pytest.fixture(scope="session")
def invite():
    with SessionLocal() as db:
        code = InviteCode(code="TEST-INVITE-0001", max_uses=2000, used_count=0)
        db.add(code)
        db.commit()
    return "TEST-INVITE-0001"


def register(client: TestClient, invite: str, email: str) -> dict:
    r = client.post(
        "/api/auth/register",
        json={"email": email, "password": "password-123", "invite_code": invite, "name": email},
    )
    assert r.status_code == 201, r.text
    return r.json()


def login(client: TestClient, email: str) -> None:
    r = client.post("/api/auth/login", json={"email": email, "password": "password-123"})
    assert r.status_code == 200, r.text


def setup_workspace(client: TestClient) -> tuple[str, dict]:
    """配置 mock 模型接入，把所有 Bot 绑定上去，返回 (chat_id, bots)。"""
    r = client.post("/api/providers", json={"vendor_id": "mock", "kind": "mock"})
    assert r.status_code == 201, r.text
    pid = r.json()["provider"]["id"]

    bots = client.get("/api/bots").json()
    for b in bots:
        client.patch(f"/api/bots/{b['id']}", json={"provider_id": pid, "model": "botly-mock"})

    chats = client.get("/api/chats").json()
    return chats[0]["id"], bots
