"""BOT-06（前半）：Bot 启停。

停用的语义是「不再参与任何新执行」，历史消息与成员关系保留：
不能被派任务、不能被 @ 直连、不能当总指挥；总指挥被停用后该群不能发起执行。
"""
from __future__ import annotations

import asyncio
import json

from sqlalchemy import select

from app.agent import orchestrator
from app.core.db import SessionLocal
from app.core.models import Message
from app.worker import process_one
from tests.conftest import register, setup_workspace


def _bots(client) -> list[dict]:
    return client.get("/api/bots").json()


def test_toggle_enabled_roundtrip(client, invite):
    register(client, invite, "bot-toggle@test.dev")
    bot = _bots(client)[0]
    assert bot["enabled"] is True

    r = client.patch(f"/api/bots/{bot['id']}", json={"enabled": False})
    assert r.status_code == 200
    assert r.json()["enabled"] is False
    assert client.get(f"/api/bots/{bot['id']}").json()["enabled"] is False

    r = client.patch(f"/api/bots/{bot['id']}", json={"enabled": True})
    assert r.status_code == 200 and r.json()["enabled"] is True


def test_cannot_make_disabled_bot_supervisor(client, invite):
    register(client, invite, "bot-sup@test.dev")
    setup_workspace(client)
    bot = _bots(client)[0]
    client.patch(f"/api/bots/{bot['id']}", json={"enabled": False})

    r = client.post("/api/chats", json={"name": "停用总指挥", "supervisor_bot_id": bot["id"]})
    assert r.status_code == 400
    assert "已停用" in r.json()["detail"]


def test_mention_disabled_bot_rejected(client, invite):
    register(client, invite, "bot-mention@test.dev")
    chat_id, bots = setup_workspace(client)
    target = bots[-1]
    client.patch(f"/api/bots/{target['id']}", json={"enabled": False})

    r = client.post(
        f"/api/chats/{chat_id}/messages",
        json={"content": "帮我看下", "mention_bot_id": target["id"]},
    )
    assert r.status_code == 409
    assert "已停用" in r.json()["detail"]


def test_disabled_supervisor_blocks_new_run(client, invite):
    register(client, invite, "bot-disabled-sup@test.dev")
    chat_id, bots = setup_workspace(client)
    sup = bots[0]
    client.patch(f"/api/bots/{sup['id']}", json={"enabled": False})

    r = client.post(f"/api/chats/{chat_id}/messages", json={"content": "开工"})
    assert r.status_code == 409
    assert "总指挥" in r.json()["detail"] and "已停用" in r.json()["detail"]


def test_delegate_to_disabled_bot_returns_hint(client, invite, monkeypatch):
    """总指挥仍想派任务给停用成员时，工具结果要明确告诉它换人。"""
    register(client, invite, "bot-delegate@test.dev")
    chat_id, bots = setup_workspace(client)
    target = next(b for b in bots if b["name"] == "研究员")
    client.patch(f"/api/bots/{target['id']}", json={"enabled": False})

    calls = {"n": 0}

    async def fake_stream(**kw):
        ctx = kw.get("ctx") or {}
        if ctx.get("role") == "worker":
            yield {"type": "done", "content": "不应执行到这里", "tool_calls": []}
            return
        calls["n"] += 1
        if calls["n"] == 1:
            yield {
                "type": "done",
                "content": "",
                "tool_calls": [
                    {
                        "id": "d-disabled",
                        "type": "function",
                        "function": {
                            "name": "delegate",
                            "arguments": json.dumps(
                                {"bot": "研究员", "instruction": "查一下资料"},
                                ensure_ascii=False,
                            ),
                        },
                    }
                ],
            }
            return
        yield {"type": "done", "content": "那就我自己来", "tool_calls": []}

    monkeypatch.setattr(orchestrator, "stream_chat", fake_stream)
    run_id = client.post(f"/api/chats/{chat_id}/messages", json={"content": "派任务"}).json()[
        "run"
    ]["id"]
    asyncio.run(process_one(run_id))

    with SessionLocal() as db:
        rows = db.scalars(
            select(Message).where(Message.run_id == run_id, Message.type == "tool_result")
        ).all()
    assert any("已停用" in (m.content or "") for m in rows)

    run = client.get(f"/api/runs/{run_id}").json()
    assert run["status"] == "completed"
