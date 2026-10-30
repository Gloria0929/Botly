"""MOD-02：模型能力探测、持久化与总指挥拦截。"""
from __future__ import annotations

import pytest

from tests.conftest import register

_LISTED = ["good-model", "bad-model"]


async def _fake_list_models(*, base_url, api_key):
    return True, list(_LISTED), "已获取 2 个模型"


@pytest.fixture
def probe(monkeypatch):
    """按模型名返回预设能力，默认两项都支持。

    `plan[model] = (streaming, tool_calling)` 可在用例里改。
    """
    plan: dict[str, tuple[bool | None, bool | None]] = {}

    async def fake_probe(*, base_url, api_key, model):
        streaming, tool = plan.get(model, (True, True))
        if tool is False:
            message = "连接正常，支持流式输出，但模型没有发起工具调用"
        else:
            message = "连接正常，支持流式输出与工具调用"
        return {
            "ok": True,
            "message": message,
            "streaming": streaming,
            "tool_calling": tool,
        }

    monkeypatch.setattr("app.api.providers.list_models", _fake_list_models)
    monkeypatch.setattr("app.api.providers.probe_capabilities", fake_probe)
    return plan


def _add_provider(client) -> dict:
    r = client.post("/api/providers", json={"vendor_id": "deepseek", "api_key": "sk-test"})
    assert r.status_code == 201, r.text
    return r.json()["provider"]


def test_save_probes_first_model_and_persists(client, invite, probe):
    register(client, invite, "cap-save@test.dev")
    r = client.post("/api/providers", json={"vendor_id": "deepseek", "api_key": "sk-test"})
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["detected"] is True
    assert "支持流式输出与工具调用" in body["message"]

    models = {m["name"]: m for m in body["provider"]["models"]}
    # 只探测首个模型，避免一保存就打满请求
    assert models["good-model"]["streaming"] is True
    assert models["good-model"]["tool_calling"] is True
    assert models["good-model"]["checked_at"]
    assert "tool_calling" not in models["bad-model"]

    caps = body["provider"]["capabilities"]
    assert caps == {
        "total": 2,
        "known": 1,
        "tool_calling": 1,
        "no_tool_calling": 0,
        "no_streaming": 0,
    }


def test_test_endpoint_persists_capability(client, invite, probe):
    register(client, invite, "cap-test@test.dev")
    provider = _add_provider(client)
    probe["good-model"] = (True, False)

    r = client.post(f"/api/providers/{provider['id']}/test")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is True
    assert body["streaming"] is True
    assert body["tool_calling"] is False

    models = {m["name"]: m for m in body["provider"]["models"]}
    assert models["good-model"]["tool_calling"] is False
    assert body["provider"]["capabilities"]["no_tool_calling"] == 1


def test_probe_endpoint_detects_all_models(client, invite, probe):
    register(client, invite, "cap-batch@test.dev")
    provider = _add_provider(client)
    probe["bad-model"] = (True, False)

    r = client.post(f"/api/providers/{provider['id']}/probe")
    assert r.status_code == 200, r.text
    body = r.json()
    assert {row["model"] for row in body["results"]} == {"good-model", "bad-model"}
    assert "1 个不支持工具调用" in body["message"]

    models = {m["name"]: m for m in body["provider"]["models"]}
    assert models["bad-model"]["tool_calling"] is False
    assert models["good-model"]["tool_calling"] is True


def test_editing_prices_keeps_capability(client, invite, probe):
    register(client, invite, "cap-price@test.dev")
    provider = _add_provider(client)

    r = client.patch(
        f"/api/providers/{provider['id']}",
        json={"models": [{"name": "good-model", "input_price": 1.5, "output_price": 2.5}]},
    )
    assert r.status_code == 200, r.text
    row = r.json()["models"][0]
    assert row["input_price"] == 1.5
    assert row["tool_calling"] is True
    assert row["checked_at"]


def test_supervisor_rejects_model_without_tool_calling(client, invite, probe):
    data = register(client, invite, "cap-sup@test.dev")
    chat_id = data["chat_id"]
    provider = _add_provider(client)
    probe["bad-model"] = (True, False)
    assert client.post(f"/api/providers/{provider['id']}/probe").status_code == 200

    bots = client.get("/api/bots").json()
    bad_bot, good_bot = bots[0], bots[1]
    client.patch(
        f"/api/bots/{bad_bot['id']}", json={"provider_id": provider["id"], "model": "bad-model"}
    )
    client.patch(
        f"/api/bots/{good_bot['id']}", json={"provider_id": provider["id"], "model": "good-model"}
    )

    r = client.patch(f"/api/chats/{chat_id}", json={"supervisor_bot_id": bad_bot["id"]})
    assert r.status_code == 400, r.text
    assert "不支持工具调用" in r.json()["detail"]

    r = client.patch(f"/api/chats/{chat_id}", json={"supervisor_bot_id": good_bot["id"]})
    assert r.status_code == 200, r.text
    assert r.json()["supervisor_bot_id"] == good_bot["id"]


def test_create_chat_skips_blocked_supervisor_template(client, invite, probe):
    register(client, invite, "cap-auto@test.dev")
    provider = _add_provider(client)
    probe["bad-model"] = (True, False)
    assert client.post(f"/api/providers/{provider['id']}/probe").status_code == 200

    bots = client.get("/api/bots").json()
    template_bot, ok_bot = bots[0], bots[1]
    assert template_bot["role"] == "总指挥"
    client.patch(
        f"/api/bots/{template_bot['id']}",
        json={"provider_id": provider["id"], "model": "bad-model"},
    )
    client.patch(
        f"/api/bots/{ok_bot['id']}", json={"provider_id": provider["id"], "model": "good-model"}
    )

    r = client.post(
        "/api/chats",
        json={"name": "自动挑选总指挥", "member_ids": [template_bot["id"], ok_bot["id"]]},
    )
    assert r.status_code == 201, r.text
    # 总指挥模板的模型不支持工具调用，自动挑一个能用的
    assert r.json()["supervisor_bot_id"] == ok_bot["id"]


def test_unknown_capability_does_not_block(client, invite, probe):
    """没探测过的模型不拦截：避免把自建网关或一次网络故障当成能力缺失。"""
    data = register(client, invite, "cap-unknown@test.dev")
    chat_id = data["chat_id"]
    provider = _add_provider(client)

    bots = client.get("/api/bots").json()
    client.patch(
        f"/api/bots/{bots[2]['id']}",
        json={"provider_id": provider["id"], "model": "bad-model"},
    )
    r = client.patch(f"/api/chats/{chat_id}", json={"supervisor_bot_id": bots[2]["id"]})
    assert r.status_code == 200, r.text
