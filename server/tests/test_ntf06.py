"""NTF-06：Web Push（Service Worker + VAPID）。

覆盖点：
- GET /api/push/config 下发可用的 applicationServerKey（65 字节未压缩 P-256 公钥）；
- 订阅注册、按端点去重（重复注册更新密钥而不是插新行）、注销；
- 非 https 端点被拒；
- events.notify 会调度 Web Push；总开关关闭时不发；
- 推送返回 404/410 时清理失效订阅；
- 生成的 VAPID 密钥能被 cryptography 正常加载。

注意：发送在后台线程执行（不阻塞请求事务），断言前统一用 `_wait` 轮询等待。
"""
from __future__ import annotations

import base64
import json
import time

import pytest

import app.services.push as push
from app.core.db import SessionLocal
from app.core.events import notify
from .conftest import register

MAIL_SUFFIX = "@test.dev"
ENDPOINT = "https://push.example.com/v1/ntf06"


class FakePushFailure(Exception):
    """带 response.status_code 的假推送异常，模拟 WebPushException。"""

    def __init__(self, status_code: int):
        super().__init__(f"push failed {status_code}")
        self.response = type("R", (), {"status_code": status_code})()


def _new_user(client, invite, tag: str) -> str:
    email = f"ntf06-{tag}{MAIL_SUFFIX}"
    register(client, invite, email)
    return email


def _user_id(client) -> str:
    return client.get("/api/auth/me").json()["id"]


def _sub(client, endpoint: str = ENDPOINT, keys: dict | None = None) -> None:
    body = {
        "endpoint": endpoint,
        "keys": keys or {"p256dh": "B-p256dh-key", "auth": "auth-secret"},
        "user_agent": "pytest",
    }
    r = client.post("/api/push/subscriptions", json=body)
    assert r.status_code == 200, r.text


def _wait(cond, timeout: float = 5.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if cond():
            return True
        time.sleep(0.02)
    return False


def test_push_config_returns_public_key(client, invite):
    _new_user(client, invite, "cfg")
    r = client.get("/api/push/config")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["enabled"] is True
    raw = base64.urlsafe_b64decode(body["public_key"] + "=" * (-len(body["public_key"]) % 4))
    # X9.62 未压缩点：0x04 前缀 + 64 字节坐标
    assert len(raw) == 65 and raw[0] == 4


def test_subscribe_dedupe_and_unsubscribe(client, invite):
    _new_user(client, invite, "sub")
    _sub(client)
    # 同一端点重复注册：更新密钥而不是新增一行
    _sub(client, keys={"p256dh": "B-new-key", "auth": "auth-2"})
    rows = client.get("/api/push/subscriptions").json()["items"]
    assert len(rows) == 1
    assert rows[0]["endpoint"] == ENDPOINT

    r = client.post("/api/push/unsubscribe", json={"endpoint": ENDPOINT})
    assert r.status_code == 200
    assert client.get("/api/push/subscriptions").json()["items"] == []
    # 再注销一次也不报错
    client.post("/api/push/unsubscribe", json={"endpoint": ENDPOINT})


def test_subscribe_rejects_non_https(client, invite):
    _new_user(client, invite, "http")
    r = client.post(
        "/api/push/subscriptions",
        json={"endpoint": "http://push.example.com/v1/x", "keys": {"p256dh": "a", "auth": "b"}},
    )
    assert r.status_code == 422
    r = client.post(
        "/api/push/subscriptions",
        json={"endpoint": "https://push.example.com/v1/x", "keys": {"p256dh": "a"}},
    )
    assert r.status_code == 422


def test_requires_login(client):
    # client 是会话级 fixture：先退掉前面用例的登录态
    client.post("/api/auth/logout")
    r = client.get("/api/push/config")
    assert r.status_code == 401


def test_notify_dispatches_push(client, invite, monkeypatch):
    _new_user(client, invite, "disp")
    _sub(client, endpoint="https://push.example.com/v1/disp")
    calls: list[dict] = []

    def fake_send(sub, payload, ttl=86400):
        calls.append({"sub": sub, "payload": json.loads(payload)})

    monkeypatch.setattr(push, "send_to_subscription", fake_send)
    with SessionLocal() as db:
        notify(db, _user_id(client), "approval.new", "需要审批", "web_search", chat_id="c1")
    assert _wait(lambda: len(calls) == 1)
    assert calls[0]["sub"]["endpoint"] == "https://push.example.com/v1/disp"
    assert calls[0]["payload"]["title"] == "需要审批"
    assert calls[0]["payload"]["type"] == "approval.new"
    assert calls[0]["payload"]["chat_id"] == "c1"
    assert "id" in calls[0]["payload"] and "created_at" in calls[0]["payload"]


def test_notify_respects_master_switch(client, invite, monkeypatch):
    _new_user(client, invite, "switch")
    _sub(client, endpoint="https://push.example.com/v1/switch")
    client.patch("/api/settings", json={"notifications_enabled": False})
    calls: list = []
    monkeypatch.setattr(push, "send_to_subscription", lambda *a, **k: calls.append(1))
    with SessionLocal() as db:
        notify(db, _user_id(client), "push.test", "不该发")
    time.sleep(0.3)
    assert calls == []
    # 重新打开后恢复发送
    client.patch("/api/settings", json={"notifications_enabled": True})
    with SessionLocal() as db:
        notify(db, _user_id(client), "push.test", "应该发")
    assert _wait(lambda: len(calls) == 1)


def test_dead_subscription_cleaned(client, invite, monkeypatch):
    _new_user(client, invite, "dead")
    _sub(client, endpoint="https://push.example.com/v1/gone")
    _sub(client, endpoint="https://push.example.com/v1/alive")

    def fake_send(sub, payload, ttl=86400):
        if sub["endpoint"].endswith("/gone"):
            raise FakePushFailure(410)

    monkeypatch.setattr(push, "send_to_subscription", fake_send)
    with SessionLocal() as db:
        notify(db, _user_id(client), "push.test", "清理失效订阅")
    assert _wait(
        lambda: [s["endpoint"] for s in client.get("/api/push/subscriptions").json()["items"]]
        == ["https://push.example.com/v1/alive"]
    )


def test_transient_failure_keeps_subscription(client, invite, monkeypatch):
    _new_user(client, invite, "transient")
    _sub(client, endpoint="https://push.example.com/v1/transient")

    def fake_send(sub, payload, ttl=86400):
        raise FakePushFailure(503)

    monkeypatch.setattr(push, "send_to_subscription", fake_send)
    with SessionLocal() as db:
        notify(db, _user_id(client), "push.test", "临时失败")
    time.sleep(0.3)
    assert len(client.get("/api/push/subscriptions").json()["items"]) == 1


def test_vapid_keys_load_and_persist():
    data = push.keys()
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ec

    priv = serialization.load_pem_private_key(data["private_pem"].encode(), password=None)
    assert priv is not None
    # 二次调用走缓存，返回同一把
    assert push.keys()["public"] == data["public"]
    # 公钥能还原成有效 P-256 公钥
    raw = base64.urlsafe_b64decode(data["public"] + "=" * (-len(data["public"]) % 4))
    pub = ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), raw)
    assert pub.curve.name == "secp256r1"
