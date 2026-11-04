"""直接群聊验收：不调用判断模型，不预选固定人数。"""
import asyncio
import json
from uuid import uuid4

import pytest
from sqlalchemy import select

from app.agent import orchestrator
from app.core.db import SessionLocal
from app.core.models import Event, Run
from app.worker import process_one, _wake_waiting_parents
from tests.conftest import register, setup_workspace


@pytest.fixture
def room(client, invite):
    register(client, invite, f"direct-{uuid4().hex[:12]}@test.dev")
    chat_id, bots = setup_workspace(client)
    assert client.patch(f"/api/chats/{chat_id}", json={"mode": "free"}).status_code == 200
    return chat_id, bots


def model(monkeypatch, replies=None):
    calls = []
    async def stream(**kwargs):
        assert kwargs["ctx"]["role"] != "chime", "不能再调用独立判断模型"
        calls.append(kwargs)
        name = kwargs["ctx"]["bot_name"]
        content = (replies or {}).get(name, "[PASS]" if kwargs["ctx"].get("observer") else name + "的回应")
        content = content(kwargs) if callable(content) else content
        await asyncio.sleep(0.005)
        for part in (content[:2], content[2:]):
            if part:
                yield {"type": "delta", "text": part}
        yield {"type": "done", "content": content, "tool_calls": [],
               "usage": {"input_tokens": 20, "output_tokens": 5}}
    monkeypatch.setattr(orchestrator, "stream_chat", stream)
    return calls


async def drain(rid):
    for _ in range(40):
        with SessionLocal() as db:
            root = db.get(Run, rid)
            if root.status in ("completed", "failed", "stopped", "waiting"):
                return root.status
            ids = list(db.scalars(select(Run.id).where(
                (Run.id == rid) | (Run.parent_run_id == rid), Run.status == "queued")))
        if ids:
            await asyncio.gather(*(process_one(item) for item in ids))
        _wake_waiting_parents()
    raise AssertionError("群聊未收尾")


def send(client, cid, content, **extra):
    response = client.post(f"/api/chats/{cid}/messages", json={"content": content, **extra})
    assert response.status_code == 201, response.text
    result = response.json()
    if result["run"]:
        assert asyncio.run(drain(result["run"]["id"])) == "completed"
    return result


def answers(client, cid):
    return [m for m in client.get(f"/api/chats/{cid}/messages?limit=200").json()["messages"]
            if m["type"] == "text" and m["sender_type"] == "bot"]


def test_direct_response_has_no_judgement_runs_or_fixed_three_speakers(client, room, monkeypatch):
    cid, bots = room
    calls = model(monkeypatch)
    result = send(client, cid, "你好")
    assert len(answers(client, cid)) == 1
    assert len(calls) == len(bots)
    assert all(c["ctx"]["role"] == "peer" for c in calls)
    with SessionLocal() as db:
        children = list(db.scalars(select(Run).where(Run.parent_run_id == result["run"]["id"])))
        assert len(children) == len(bots)
        assert all(child.kind == "free" for child in children)
    assert sum(not c["ctx"]["observer"] for c in calls) == 1


def test_speaker_count_follows_actual_responses_instead_of_filling_limit(client, room, monkeypatch):
    cid, bots = room
    model(monkeypatch, {bots[1]["name"]: "我有一个补充"})
    send(client, cid, "聊聊")
    assert len(answers(client, cid)) == 2
    assert {m["sender_bot_id"] for m in answers(client, cid)} == {bots[0]["id"], bots[1]["id"]}
    assert all(m["content"] != "[PASS]" for m in answers(client, cid))


def test_ordinary_followup_continues_with_last_interlocutor(client, room, monkeypatch):
    cid, bots = room
    calls = model(monkeypatch)
    send(client, cid, f"@{bots[1]['name']} 说说你的看法")
    calls.clear()
    send(client, cid, "继续说说")
    rows = answers(client, cid)
    assert len(rows) == 2 and all(m["sender_bot_id"] == bots[1]["id"] for m in rows)
    assert next(c for c in calls if not c["ctx"]["observer"])["ctx"]["bot_name"] == bots[1]["name"]


def test_mentions_reply_and_muted(client, room, monkeypatch):
    cid, bots = room
    calls = model(monkeypatch)
    client.patch(f"/api/chats/{cid}", json={"muted": True})
    assert send(client, cid, "普通消息")["run"] is None
    assert not calls
    send(client, cid, f"@{bots[1]['name']}，你好")
    row = answers(client, cid)[0]
    send(client, cid, "继续说说", reply_to=row["id"])
    assert [m["sender_bot_id"] for m in answers(client, cid)] == [bots[1]["id"]] * 2
    assert len(calls) == 2


def test_everyone_once_and_no_judgement(client, room, monkeypatch):
    cid, bots = room
    calls = model(monkeypatch, {bot["name"]: f"@{bots[-1]['name']} 收到" for bot in bots})
    client.patch(f"/api/chats/{cid}", json={"max_speakers": 1})
    send(client, cid, "@everyone 大家来开会")
    assert len(calls) == len(bots)
    assert len(answers(client, cid)) == len(bots)
    assert {m["sender_bot_id"] for m in answers(client, cid)} == {bot["id"] for bot in bots}


def test_muting_before_members_start_skips_model_calls(client, room, monkeypatch):
    cid, _ = room
    calls = model(monkeypatch)
    result = client.post(f"/api/chats/{cid}/messages", json={"content": "你好"}).json()
    rid = result["run"]["id"]
    asyncio.run(process_one(rid))
    client.patch(f"/api/chats/{cid}", json={"muted": True})
    assert asyncio.run(drain(rid)) == "completed"
    assert not calls and not answers(client, cid)


def test_no_preannounced_thinking_for_silent_members(client, room):
    cid, bots = room
    result = client.post(f"/api/chats/{cid}/messages", json={"content": "你好"}).json()
    rid = result["run"]["id"]
    asyncio.run(process_one(rid))
    snapshot = client.get(f"/api/chats/{cid}").json()
    assert sum(bot["status"] == "working" for bot in snapshot["members"]) == 1
    with SessionLocal() as db:
        assert not db.scalar(select(Event.id).where(Event.chat_id == cid, Event.type == "bot.typing"))
    assert client.post(f"/api/runs/{rid}/stop").status_code == 200


def test_pass_is_not_published_and_primary_needs_no_second_call(client, room, monkeypatch):
    cid, bots = room
    calls = model(monkeypatch, {bot["name"]: "[PASS]" for bot in bots})
    send(client, cid, "你好")
    assert len(calls) == len(bots)
    assert len(answers(client, cid)) == 1
    with SessionLocal() as db:
        events = list(db.scalars(select(Event).where(Event.chat_id == cid, Event.type == "message.created")))
        assert not any("[PASS]" in (event.payload.get("content") or "") for event in events)


@pytest.mark.parametrize("limit", [1, 3, 6])
def test_all_members_can_generate_but_publication_limit_is_atomic(client, room, monkeypatch, limit):
    cid, bots = room
    provider = client.get(f"/api/bots/{bots[0]['id']}").json()["provider_id"]
    for number in range(3):
        response = client.post("/api/bots", json={"name": f"同事{number}", "provider_id": provider, "model": "botly-mock"})
        assert response.status_code == 201
        bot = response.json()
        assert client.post(f"/api/chats/{cid}/members", json={"bot_ids": [bot["id"]]}).status_code == 200
    client.patch(f"/api/chats/{cid}", json={"max_speakers": limit})
    started = []
    async def verify():
        together = asyncio.Event()
        async def stream(**kwargs):
            started.append(kwargs["ctx"]["bot_name"])
            if len(started) == 6:
                together.set()
            await asyncio.wait_for(together.wait(), 2)
            if kwargs["ctx"].get("required_reply"):
                await asyncio.sleep(0.03)
            text = kwargs["ctx"]["bot_name"] + "的看法"
            yield {"type": "delta", "text": text}
            yield {"type": "done", "content": text, "tool_calls": [], "usage": {"input_tokens": 10, "output_tokens": 5}}
        monkeypatch.setattr(orchestrator, "stream_chat", stream)
        result = client.post(f"/api/chats/{cid}/messages", json={"content": "大家聊聊"}).json()
        assert await drain(result["run"]["id"]) == "completed"
    asyncio.run(verify())
    rows = answers(client, cid)
    assert len(started) == 6, "不能预先只挑三位成员"
    assert len(rows) == limit
    assert bots[0]["id"] in {m["sender_bot_id"] for m in rows}, "主要对话对象不能被快成员挤掉"


def test_handoffs_are_natural_mentions_and_stop_at_round_limit(client, room, monkeypatch):
    cid, bots = room
    model(monkeypatch, {bots[1]["name"]: f"@{bots[2]['name']} 请你补充",
                        bots[2]["name"]: f"@{bots[1]['name']} 我补充了"})
    send(client, cid, f"@{bots[1]['name']} 看看")
    assert [m["sender_bot_id"] for m in answers(client, cid)] == [bots[1]["id"], bots[2]["id"]]


def test_messages_and_member_replies_remain_parallel(client, room, monkeypatch):
    cid, bots = room
    contents = [f"@{bots[1]['name']} 第一条", f"@{bots[1]['name']} 第二条"]
    started = []
    async def verify():
        together = asyncio.Event()
        async def stream(**kwargs):
            current = [m["content"] for m in kwargs["messages"] if m["role"] == "user" and m["content"] in contents]
            assert len(current) == 1
            started.append(current[0])
            if len(started) == 2:
                together.set()
            await asyncio.wait_for(together.wait(), 2)
            yield {"type": "done", "content": "回复" + current[0], "tool_calls": [], "usage": {"input_tokens": 10, "output_tokens": 5}}
        monkeypatch.setattr(orchestrator, "stream_chat", stream)
        ids = []
        for content in contents:
            response = client.post(f"/api/chats/{cid}/messages", json={"content": content})
            assert response.status_code == 201
            ids.append(response.json()["run"]["id"])
        assert await asyncio.gather(*(drain(rid) for rid in ids)) == ["completed", "completed"]
    asyncio.run(verify())
    assert len(answers(client, cid)) == 2


def test_restart_after_saved_output_does_not_publish_twice(client, room, monkeypatch):
    from app.agent.signals import LeaseLost
    cid, bots = room
    calls = model(monkeypatch)
    original = orchestrator.RunExecutor._finalize_message
    crashed = []
    def finalize(self, *args, **kwargs):
        original(self, *args, **kwargs)
        if kwargs.get("mark_turn") and not crashed:
            crashed.append(self.run_id)
            raise LeaseLost()
    monkeypatch.setattr(orchestrator.RunExecutor, "_finalize_message", finalize)
    result = client.post(f"/api/chats/{cid}/messages", json={"content": "你好"}).json()
    rid = result["run"]["id"]
    asyncio.run(process_one(rid))
    with SessionLocal() as db:
        ids = list(db.scalars(select(Run.id).where(Run.parent_run_id == rid, Run.status == "queued")))
    for child in ids:
        asyncio.run(process_one(child))
    assert crashed
    with SessionLocal() as db:
        child = db.get(Run, crashed[0])
        child.status = "queued"
        child.worker_id = None
        child.lease_until = None
        db.commit()
    assert asyncio.run(drain(rid)) == "completed"
    assert len(answers(client, cid)) == 1
    assert len(calls) == len(bots)


def test_observers_cannot_repeat_the_primary_task(client, room, monkeypatch):
    cid, bots = room
    calls = model(monkeypatch)
    send(client, cid, "帮我整理一个文档")
    observers = [call for call in calls if call["ctx"].get("observer")]
    assert observers and all(not call["tools"] for call in observers)
    assert all(call["max_output_tokens"] == 256 for call in observers)


def test_explicit_peer_can_delegate_and_continue_without_leader(client, room, monkeypatch):
    cid, bots = room
    client.patch(f"/api/chats/{cid}", json={"supervisor_bot_id": None})
    roles = []
    async def stream(**kwargs):
        role = kwargs["ctx"]["role"]
        roles.append(role)
        if len(roles) == 1:
            yield {"type": "done", "content": "我请同伴帮忙。", "tool_calls": [{"id": "direct-handoff",
                "type": "function", "function": {"name": "delegate", "arguments": json.dumps({
                    "bot": bots[2]["name"], "instruction": "检查实现", "expected_output": "实现建议"})}}],
                "usage": {"input_tokens": 20, "output_tokens": 5}}
        else:
            yield {"type": "done", "content": "任务详细结果" if role == "worker" else "同伴检查过了，我补充结论。",
                   "tool_calls": [], "usage": {"input_tokens": 20, "output_tokens": 5}}
    monkeypatch.setattr(orchestrator, "stream_chat", stream)
    send(client, cid, f"@{bots[1]['name']} 看看实现")
    assert roles == ["peer", "worker", "peer"]


def test_direct_replies_share_the_message_token_budget(client, room, monkeypatch):
    cid, _ = room
    client.patch(f"/api/chats/{cid}", json={"limits_override": {"token_budget": 1000}})
    async def stream(**kwargs):
        yield {"type": "done", "content": "[PASS]" if kwargs["ctx"].get("observer") else "我有个看法。",
               "tool_calls": [], "usage": {"input_tokens": 600, "output_tokens": 10}}
    monkeypatch.setattr(orchestrator, "stream_chat", stream)
    result = client.post(f"/api/chats/{cid}/messages", json={"content": "聊聊"}).json()
    assert asyncio.run(drain(result["run"]["id"])) == "failed"
    assert "token" in client.get(f"/api/runs/{result['run']['id']}").json()["end_reason"]


def test_old_judgement_queue_is_retired_without_model_calls(client, room, monkeypatch):
    cid, bots = room
    calls = model(monkeypatch)
    result = client.post(f"/api/chats/{cid}/messages", json={"content": "你好"}).json()
    rid = result["run"]["id"]
    with SessionLocal() as db:
        root = db.get(Run, rid)
        old = Run(user_id=root.user_id, chat_id=cid, kind="chime", parent_run_id=rid, target_bot_id=bots[1]["id"])
        db.add(old)
        db.commit()
        old_id = old.id
    assert asyncio.run(drain(rid)) == "completed"
    with SessionLocal() as db:
        assert db.get(Run, old_id).status == "completed"
    assert all(call["ctx"]["role"] == "peer" for call in calls)


def test_task_mode_and_free_validation_stay_compatible(client, invite):
    register(client, invite, f"direct-validate-{uuid4().hex[:12]}@test.dev")
    cid, bots = setup_workspace(client)
    assert client.get(f"/api/chats/{cid}").json()["mode"] == "task"
    assert client.post("/api/chats", json={"name": "太少", "mode": "free", "member_ids": [bots[0]["id"]]}).status_code == 400
    assert client.patch(f"/api/chats/{cid}", json={"max_speakers": 7}).status_code == 422
    assert client.patch(f"/api/bots/{bots[0]['id']}", json={"proactivity": 1.1}).status_code == 422
