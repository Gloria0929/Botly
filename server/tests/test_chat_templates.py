"""群聊模板一键创建：成员复用、缺失角色补齐、配置继承与账号隔离。"""
from __future__ import annotations

import pytest
from sqlalchemy import select

from app.core.db import SessionLocal
from app.core.models import Bot, Chat, Provider
from app.services.chat_templates import CHAT_TEMPLATES
from tests.conftest import register, setup_workspace


@pytest.mark.parametrize("template", CHAT_TEMPLATES, ids=lambda t: t["id"])
def test_create_each_template_reuses_members_and_configures_missing_roles(client, invite, template):
    register(client, invite, f"template-{template['id']}@test.dev")
    _, bots = setup_workspace(client)
    original = {b["id"]: client.get(f"/api/bots/{b['id']}").json() for b in bots}
    supervisor = next(b for b in original.values() if b["role"] == "总指挥")
    response = client.post("/api/chats/from-template", json={"template_id": template["id"]})
    assert response.status_code == 201, response.text
    chat = response.json()
    assert chat["name"] == template["name"]
    assert chat["supervisor_bot_id"] == supervisor["id"]
    assert len(chat["members"]) == len(template["members"])
    assert chat["model_setup_required"] is False
    for member in chat["members"]:
        assert member["provider_id"] == supervisor["provider_id"] and member["model"] == supervisor["model"]
        if member["id"] in original:
            assert client.get(f"/api/bots/{member['id']}").json() == original[member["id"]]
        else:
            assert member["id"] in chat["created_bot_ids"]
            if member["role"] in ("程序员", "数据分析师"):
                assert {"code_exec", "run_command"} <= set(member["tools"])
    detail = client.get(f"/api/chats/{chat['id']}").json()
    assert detail["main_thread_id"] and detail["active_run"] is None
    again = client.post("/api/chats/from-template", json={"template_id": template["id"]}).json()
    assert again["id"] != chat["id"]
    assert again["created_bot_ids"] == []
    assert {b["id"] for b in again["members"]} == {b["id"] for b in chat["members"]}


def test_catalog_is_authenticated_and_unknown_template_creates_nothing(client, invite):
    client.post("/api/auth/logout")
    assert client.get("/api/chats/templates").status_code == 401
    register(client, invite, "template-catalog@test.dev")
    response = client.get("/api/chats/templates")
    assert response.status_code == 200
    assert len(response.json()) == 5
    assert all(t["members"][0]["role"] == "总指挥" for t in response.json())
    before = client.get("/api/bots").json(), client.get("/api/chats").json()
    assert client.post("/api/chats/from-template", json={"template_id": "missing"}).status_code == 404
    assert (client.get("/api/bots").json(), client.get("/api/chats").json()) == before


def test_template_works_without_existing_members_or_model(client, invite):
    registered = register(client, invite, "template-empty@test.dev")
    for chat in client.get("/api/chats").json():
        assert client.delete(f"/api/chats/{chat['id']}").status_code == 200
    for bot in client.get("/api/bots").json():
        assert client.delete(f"/api/bots/{bot['id']}").status_code == 200
    chat = client.post("/api/chats/from-template", json={"template_id": "software-development"}).json()
    assert len(chat["created_bot_ids"]) == 3 and chat["model_setup_required"] is True
    assert all(b["provider_id"] is None for b in chat["members"])
    with SessionLocal() as db:
        assert all(db.get(Bot, bid).user_id == registered["id"] for bid in chat["created_bot_ids"])


def test_template_skips_disabled_deleted_and_foreign_members(client, invite):
    register(client, invite, "template-foreign@test.dev")
    _, foreign = setup_workspace(client)
    register(client, invite, "template-ownership@test.dev")
    _, own = setup_workspace(client)
    researcher = next(b for b in own if b["role"] == "研究员")
    writer = next(b for b in own if b["role"] == "架构师")
    client.patch(f"/api/bots/{researcher['id']}", json={"enabled": False})
    client.delete(f"/api/bots/{writer['id']}")
    response = client.post("/api/chats/from-template", json={"template_id": "content-writing"})
    assert response.status_code == 201, response.text
    ids = {b["id"] for b in response.json()["members"]}
    assert not ids & {b["id"] for b in foreign}
    assert researcher["id"] not in ids and writer["id"] not in ids
    assert client.get(f"/api/bots/{researcher['id']}").json()["enabled"] is False


def test_template_uses_compatible_model_without_rewriting_existing_bot(client, invite):
    register(client, invite, "template-capability@test.dev")
    _, bots = setup_workspace(client)
    supervisor = next(b for b in bots if b["role"] == "总指挥")
    supervisor = client.get(f"/api/bots/{supervisor['id']}").json()
    with SessionLocal() as db:
        provider = db.get(Provider, supervisor["provider_id"])
        provider.models = [{"name": "bad", "tool_calling": False}, {"name": "good", "tool_calling": True, "streaming": True}]
        for row in db.scalars(select(Bot).where(Bot.user_id == provider.user_id)):
            row.model = "bad"
        db.commit()
    response = client.post("/api/chats/from-template", json={"template_id": "software-development"})
    assert response.status_code == 201, response.text
    assert all(b["model"] == "good" for b in response.json()["members"])
    assert client.get(f"/api/bots/{supervisor['id']}").json()["model"] == "bad"


def test_failed_template_creation_rolls_back_added_members(client, invite, monkeypatch):
    from app.api import chats
    from fastapi import HTTPException

    registered = register(client, invite, "template-rollback@test.dev")
    before = len(client.get("/api/bots").json())

    def fail(*args):
        raise HTTPException(400, "模拟创建失败")

    monkeypatch.setattr(chats, "create_chat", fail)
    assert client.post("/api/chats/from-template", json={"template_id": "data-analysis"}).status_code == 400
    assert len(client.get("/api/bots").json()) == before
    with SessionLocal() as db:
        assert db.scalar(select(Chat).where(Chat.user_id == registered["id"], Chat.name == "数据分析")) is None
