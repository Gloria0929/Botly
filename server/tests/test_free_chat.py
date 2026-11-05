"""直接群聊验收：不调用判断模型，不预选固定人数。"""
import asyncio
import json
from uuid import uuid4

import pytest
from sqlalchemy import select

from app.agent import orchestrator
from app.core.db import SessionLocal
from app.core.models import Event, Message, Run
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
    for _ in range(300):
        with SessionLocal() as db:
            root = db.get(Run, rid)
            if root.status in ("completed", "failed", "stopped", "waiting"):
                return root.status
            ids = list(db.scalars(select(Run.id).where(
                (Run.id == rid) | (Run.parent_run_id == rid), Run.status == "queued")))
        if ids:
            await asyncio.gather(*(process_one(item) for item in ids))
        _wake_waiting_parents()
        await asyncio.sleep(0.01)  # 被前一条消息挡住时等待下一轮，不能空转耗尽轮数。
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


@pytest.mark.parametrize("mention", ["@全员 ", "@全员，", "@everyone "])
def test_everyone_once_and_no_judgement(client, room, monkeypatch, mention):
    cid, bots = room
    calls = model(monkeypatch, {bot["name"]: f"@{bots[-1]['name']} 收到" for bot in bots})
    client.patch(f"/api/chats/{cid}", json={"max_speakers": 1})
    send(client, cid, mention + "大家来开会")
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
    assert sum(bot["status"] == "working" for bot in snapshot["members"]) == 0
    with SessionLocal() as db:
        assert not db.scalar(select(Event.id).where(Event.chat_id == cid, Event.type == "bot.typing"))
    assert client.post(f"/api/runs/{rid}/stop").status_code == 200


def test_silent_primary_falls_back_without_publishing_a_fake_reply(client, room, monkeypatch):
    cid, bots = room
    calls = model(monkeypatch, {bots[0]["name"]: "[PASS]"})
    send(client, cid, "你好")
    assert len(calls) == len(bots)
    assert len(answers(client, cid)) == 1
    assert answers(client, cid)[0]["sender_bot_id"] == bots[1]["id"]
    assert answers(client, cid)[0]["content"] != "我在，收到你的消息。"
    with SessionLocal() as db:
        events = list(db.scalars(select(Event).where(Event.chat_id == cid, Event.type == "message.created")))
        assert not any("[PASS]" in (event.payload.get("content") or "") for event in events)


@pytest.mark.parametrize("limit", [1, 3, 6])
def test_supplements_stop_generating_when_speaker_limit_is_reached(client, room, monkeypatch, limit):
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
        async def stream(**kwargs):
            started.append(kwargs["ctx"]["bot_name"])
            text = kwargs["ctx"]["bot_name"] + "的看法"
            yield {"type": "delta", "text": text}
            yield {"type": "done", "content": text, "tool_calls": [], "usage": {"input_tokens": 10, "output_tokens": 5}}
        monkeypatch.setattr(orchestrator, "stream_chat", stream)
        result = client.post(f"/api/chats/{cid}/messages", json={"content": "大家聊聊"}).json()
        assert await drain(result["run"]["id"]) == "completed"
    asyncio.run(verify())
    rows = answers(client, cid)
    assert len(started) == limit, "人数达到上限后不再生成无法发布的补充"
    assert len(rows) == limit
    assert bots[0]["id"] in {m["sender_bot_id"] for m in rows}, "主要对话对象不能被快成员挤掉"


def test_handoffs_are_natural_mentions_and_stop_at_round_limit(client, room, monkeypatch):
    cid, bots = room
    model(monkeypatch, {bots[1]["name"]: f"@{bots[2]['name']} 请你补充",
                        bots[2]["name"]: f"@{bots[1]['name']} 我补充了"})
    send(client, cid, f"@{bots[1]['name']} 看看")
    assert [m["sender_bot_id"] for m in answers(client, cid)] == [bots[1]["id"], bots[2]["id"]]


def test_same_member_replies_in_order_with_previous_response_in_context(client, room, monkeypatch):
    cid, bots = room
    contents = [f"@{bots[1]['name']} 第一条", f"@{bots[1]['name']} 第二条"]
    started = []
    contexts = []
    async def verify():
        async def stream(**kwargs):
            current = [m["content"] for m in kwargs["messages"] if m["role"] == "user" and m["content"] in contents]
            assert len(current) == 1
            started.append(current[0])
            contexts.append(json.dumps(kwargs["messages"], ensure_ascii=False))
            await asyncio.sleep(0.03)
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
    assert started == contents
    assert "回复" + contents[0] in contexts[1]
    assert contents[1] not in contexts[0]


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
            yield {"type": "done", "content": "任务详细结果" if role == "worker" else f"@{bots[2]['name']} 已经检查过了，我补充结论。",
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


def test_supplements_read_primary_and_previous_supplements(client, room, monkeypatch):
    cid, bots = room
    seen = []
    contexts = []
    def reply(kw):
        blob = json.dumps(kw["messages"], ensure_ascii=False)
        contexts.append(blob)
        text = "先说明主要结论" if not kw["ctx"]["observer"] else kw["ctx"]["bot_name"] + "补充独有事实"
        seen.append(text)
        return text
    model(monkeypatch, {b["name"]: reply for b in bots})
    send(client, cid, "谈谈这个方案")
    assert len(seen) == len(bots)
    assert all(seen[0] in blob for blob in contexts[1:])
    assert seen[1] in contexts[2]
    assert [m["content"] for m in answers(client, cid)] == seen


def test_supplement_does_not_steal_conversation_partner(client, room, monkeypatch):
    cid, bots = room
    calls = model(monkeypatch, {bots[2]["name"]: "我补充一点"})
    send(client, cid, f"@{bots[1]['name']} 你觉得呢")
    send(client, cid, "继续")
    calls.clear()
    send(client, cid, "你刚才说的理由呢")
    assert next(c for c in calls if not c["ctx"]["observer"])["ctx"]["bot_name"] == bots[1]["name"]


def test_handoff_identifies_the_request_and_the_speaker(client, room, monkeypatch):
    cid, bots = room
    def receive(kw):
        blob = json.dumps(kw["messages"], ensure_ascii=False)
        assert "请你核对预算，不要重复技术选型" in blob
        assert bots[1]["name"] in blob
        assert "本轮接话请求" in blob
        return "预算已核对"
    model(monkeypatch, {bots[1]["name"]: f"@{bots[2]['name']} 请你核对预算，不要重复技术选型",
                        bots[2]["name"]: receive})
    result = send(client, cid, f"@{bots[1]['name']} 帮我看看方案")
    rows = answers(client, cid)
    assert rows[0]["meta"]["reply_to"] == result["message"]["id"]
    assert rows[1]["meta"]["reply_to"] == rows[0]["id"]


def test_incidental_or_quoted_mentions_do_not_dispatch(client, room, monkeypatch):
    cid, bots = room
    calls = model(monkeypatch, {bots[1]["name"]:
        f"我同意 @{bots[2]['name']} 的观点。\n> @{bots[2]['name']} 这是引用\n```\n@{bots[2]['name']} 示例\n```"})
    send(client, cid, f"@{bots[1]['name']} 你觉得呢")
    assert len(calls) == 1


def test_identical_optional_reply_is_silent_without_claiming_a_slot(client, room, monkeypatch):
    cid, bots = room
    model(monkeypatch, {b["name"]: "同一个结论" for b in bots})
    result = send(client, cid, "你们怎么看")
    assert [m["content"] for m in answers(client, cid)] == ["同一个结论"]
    with SessionLocal() as db:
        state = db.get(Run, result["run"]["id"]).config_snapshot["free"]
        assert len(state["speaker_claims"]) == 1


def test_new_message_skips_stale_optional_replies(client, room, monkeypatch):
    cid, bots = room
    calls = model(monkeypatch, {b["name"]: b["name"] + "的观点" for b in bots})
    first = client.post(f"/api/chats/{cid}/messages", json={"content": "第一条话题"}).json()
    rid = first["run"]["id"]
    asyncio.run(process_one(rid))
    with SessionLocal() as db:
        children = list(db.scalars(select(Run.id).where(Run.parent_run_id == rid)))
    assert len(children) == 1, "普通消息先安排接话对象"
    asyncio.run(process_one(children[0]))
    newer = client.post(f"/api/chats/{cid}/messages", json={"content": f"@{bots[1]['name']} 换个话题"}).json()
    assert asyncio.run(drain(rid)) == "completed"
    assert len(calls) == 1, "用户已经发了新消息，不再补发旧话题的旁观发言"
    assert asyncio.run(drain(newer["run"]["id"])) == "completed"


def test_pending_direct_message_keeps_followup_partner(client, room, monkeypatch):
    cid, bots = room
    calls = model(monkeypatch)
    first = client.post(f"/api/chats/{cid}/messages", json={"content": f"@{bots[1]['name']} 第一问"}).json()
    second = client.post(f"/api/chats/{cid}/messages", json={"content": "再补充一问"}).json()
    async def work():
        assert await asyncio.gather(drain(second["run"]["id"]), drain(first["run"]["id"])) == ["completed"] * 2
    asyncio.run(work())
    assert {c["ctx"]["bot_name"] for c in calls if not c["ctx"]["observer"]} == {bots[1]["name"]}


def test_different_members_can_answer_different_messages_concurrently(client, room, monkeypatch):
    cid, bots = room
    async def work():
        together = asyncio.Event()
        started = []
        async def stream(**kw):
            started.append(kw["ctx"]["bot_name"])
            if len(started) == 2:
                together.set()
            await asyncio.wait_for(together.wait(), 2)
            yield {"type": "done", "content": kw["ctx"]["bot_name"] + "的独立回答", "tool_calls": []}
        monkeypatch.setattr(orchestrator, "stream_chat", stream)
        ids = [client.post(f"/api/chats/{cid}/messages", json={"content": f"@{bot['name']} 你的问题"}).json()["run"]["id"]
               for bot in bots[1:]]
        assert await asyncio.gather(*(drain(rid) for rid in ids)) == ["completed", "completed"]
        assert set(started) == {b["name"] for b in bots[1:]}
    asyncio.run(work())


def test_supplement_generated_during_new_message_is_not_published(client, room, monkeypatch):
    cid, bots = room
    newer = []
    def reply(kw):
        if kw["ctx"]["observer"] and not newer:
            newer.append(client.post(f"/api/chats/{cid}/messages", json={"content": f"@{bots[0]['name']} 换个话题"}).json())
            return "这条过时补充不应出现"
        return "主要回答"
    model(monkeypatch, {b["name"]: reply for b in bots})
    send(client, cid, "第一个话题")
    assert all("过时补充" not in m["content"] for m in answers(client, cid))
    assert newer
    assert asyncio.run(drain(newer[0]["run"]["id"])) == "completed"


def test_free_chat_uses_conversation_tools_without_plan_or_question_cards(client, room, monkeypatch):
    cid, _ = room
    calls = model(monkeypatch)
    send(client, cid, "我们讨论一下")
    tools = {t["function"]["name"] for c in calls for t in (c["tools"] or [])}
    assert "propose_plan" not in tools and "ask_user" not in tools
    assert "delegate" in tools


def test_late_handoff_does_not_overlap_a_newer_reply_by_same_member(client, room):
    from app.worker import _claim
    cid, bots = room
    first = client.post(f"/api/chats/{cid}/messages", json={"content": f"@{bots[0]['name']} 第一问"}).json()
    second = client.post(f"/api/chats/{cid}/messages", json={"content": f"@{bots[1]['name']} 第二问"}).json()
    for item in (first, second):
        asyncio.run(process_one(item["run"]["id"]))
    with SessionLocal() as db:
        current = db.scalar(select(Run).where(Run.parent_run_id == second["run"]["id"]))
        current_id = current.id
    assert _claim(current_id, "current-member")
    with SessionLocal() as db:
        root = db.get(Run, first["run"]["id"])
        late = Run(user_id=root.user_id, chat_id=cid, kind="free", parent_run_id=root.id,
                   trigger_message_id=root.trigger_message_id, target_bot_id=bots[1]["id"])
        db.add(late)
        db.commit()
        late_id = late.id
    assert not _claim(late_id, "late-member")
    with SessionLocal() as db:
        db.get(Run, current_id).status = "completed"
        db.commit()
    assert _claim(late_id, "late-member")
    with SessionLocal() as db:
        for run in db.scalars(select(Run).where(Run.chat_id == cid)):
            run.status = "stopped"
        db.commit()


@pytest.mark.parametrize("tool_name", ["propose_plan", "ask_user"])
def test_free_chat_rejects_unadvertised_task_cards_and_continues(client, room, monkeypatch, tool_name):
    cid, bots = room
    turns = []
    async def stream(**kw):
        turns.append(kw)
        if len(turns) == 1:
            args = {"question": "需要确认吗"} if tool_name == "ask_user" else {"steps": [
                {"title": "讨论", "bot": bots[1]["name"], "depends_on": []},
            ]}
            yield {"type": "done", "content": "", "tool_calls": [{"id": "wrong-mode", "type": "function",
                "function": {"name": tool_name, "arguments": json.dumps(args)}}]}
        else:
            yield {"type": "done", "content": "我们直接聊聊这个想法。", "tool_calls": []}
    monkeypatch.setattr(orchestrator, "stream_chat", stream)
    send(client, cid, f"@{bots[0]['name']} 来聊聊")
    assert len(turns) == 2 and "自由群聊中请直接回应" in json.dumps(turns[1]["messages"], ensure_ascii=False)
    with SessionLocal() as db:
        assert not db.scalar(select(Message.id).where(Message.thread_id == answers(client, cid)[0]["thread_id"],
                                                       Message.type.in_(("plan", "question"))))
