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
    result = send(client, cid, "聊聊这个方案")
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
    assert len(calls) == 2
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
    result = client.post(f"/api/chats/{cid}/messages", json={"content": "聊聊这个方案"}).json()
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
    assert seen[0] not in contexts[1], "首批成员不等待主回复完整生成"
    assert seen[0] in contexts[2]
    assert seen[1] in contexts[2]
    rows = answers(client, cid)
    assert {m["content"] for m in rows[:2]} == set(seen[:2])
    assert rows[2]["content"] == seen[2]


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
    assert len(children) == 2, "普通消息首批安排两位成员并行接话"
    with SessionLocal() as db:
        primary = next(child_id for child_id in children
                       if db.get(Run, child_id).config_snapshot["free"]["selected"]["required"])
    asyncio.run(process_one(primary))
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


@pytest.mark.parametrize("content", ["你好 👋", "好的", "晚安", "哈哈"])
def test_social_messages_do_not_poll_the_whole_room(client, room, monkeypatch, content):
    cid, bots = room
    calls = model(monkeypatch)
    result = send(client, cid, content)
    assert len(calls) == 1 and calls[0]["ctx"]["bot_name"] == bots[0]["name"]
    assert len(answers(client, cid)) == 1
    with SessionLocal() as db:
        assert len(list(db.scalars(select(Run.id).where(Run.parent_run_id == result["run"]["id"])))) == 1


def test_greeting_with_a_real_question_still_invites_other_perspectives(client, room, monkeypatch):
    cid, bots = room
    calls = model(monkeypatch, {bots[1]["name"]: "要把维护成本也算进去。"})
    send(client, cid, "你好，这个方案还有什么风险？")
    assert len(calls) == len(bots)
    assert len(answers(client, cid)) == 2


def test_free_prompt_preserves_persona_without_builtin_task_rituals(client, room, monkeypatch):
    cid, bots = room
    client.patch(f"/api/bots/{bots[1]['id']}", json={"persona": "直率、有点幽默，喜欢用生活例子解释"})
    calls = model(monkeypatch)
    send(client, cid, f"@{bots[1]['name']} 你觉得这个想法怎么样")
    system = calls[0]["messages"][0]["content"]
    assert "直率、有点幽默" in system
    assert "收到任务后先用 web_search" not in system
    assert "本次执行的 token 预算" not in system
    assert "不同看法就直接讲理由" in system
    assert "闲聊不必先搜索或读取文件" in system


def test_free_prompt_keeps_custom_role_instructions(client, room, monkeypatch):
    cid, bots = room
    client.patch(f"/api/bots/{bots[1]['id']}", json={"system_prompt": "你擅长海洋生态，用生动的类比解释科学。"})
    calls = model(monkeypatch)
    send(client, cid, f"@{bots[1]['name']} 珊瑚为什么会白化")
    assert "你擅长海洋生态，用生动的类比解释科学。" in calls[0]["messages"][0]["content"]


def test_supplements_reply_to_the_latest_member_instead_of_starting_over(client, room, monkeypatch):
    cid, bots = room
    calls = model(monkeypatch, {
        bots[0]["name"]: "可以先用小范围试点验证。",
        bots[1]["name"]: "试点也得把维护成本算进去。",
        bots[2]["name"]: "维护可以先由现有团队轮值。",
    })
    result = send(client, cid, "聊聊这个方案怎么落地")
    rows = answers(client, cid)
    assert rows[0]["meta"]["reply_to"] == result["message"]["id"]
    assert rows[1]["meta"]["reply_to"] == result["message"]["id"]
    assert rows[2]["meta"]["reply_to"] == rows[1]["id"]
    previous, current = rows[1], calls[2]
    context = json.dumps(current["messages"], ensure_ascii=False)
    assert previous["content"] in context
    assert "现在接「" in context and "无需重新回答原问题" in context
    # 群友原话只能作为对话资料，不能被提升为系统指令。
    assert all(previous["content"] not in item["content"] for item in current["messages"] if item["role"] == "system")


def test_open_question_can_return_to_an_earlier_speaker_without_at_mentions(client, room, monkeypatch):
    cid, bots = room
    def first(kw):
        return "可以从十位活跃用户开始。" if kw["ctx"]["observer"] else "我倾向于先做小范围试点。"
    model(monkeypatch, {bots[0]["name"]: first, bots[1]["name"]: "试点该怎么选人？"})
    result = send(client, cid, "我们聊聊怎么开始")
    rows = answers(client, cid)
    assert len(rows) == 3
    assert {m["sender_bot_id"] for m in rows[:2]} == {bots[0]["id"], bots[1]["id"]}
    assert rows[2]["sender_bot_id"] == bots[0]["id"]
    question = next(m for m in rows[:2] if m["sender_bot_id"] == bots[1]["id"])
    assert rows[2]["meta"]["reply_to"] == question["id"]
    with SessionLocal() as db:
        children = list(db.scalars(select(Run).where(Run.parent_run_id == result["run"]["id"])))
        assert max(child.round for child in children) <= 2
        assert len(children) <= len(bots) * 2


def test_inline_invitation_can_hand_the_conversation_to_a_peer(client, room, monkeypatch):
    cid, bots = room
    model(monkeypatch, {bots[1]["name"]: f"这个成本我拿不准，@{bots[2]['name']} 你觉得该怎么定？",
                        bots[2]["name"]: "先按现有团队的人力成本估算。"})
    send(client, cid, f"@{bots[1]['name']} 聊聊报价")
    rows = answers(client, cid)
    assert [m["sender_bot_id"] for m in rows] == [bots[1]["id"], bots[2]["id"]]
    assert rows[1]["meta"]["reply_to"] == rows[0]["id"]


def test_natural_name_address_changes_the_conversation_partner(client, room, monkeypatch):
    cid, bots = room
    calls = model(monkeypatch)
    send(client, cid, f"@{bots[1]['name']} 先说说背景")
    calls.clear()
    send(client, cid, f"{bots[2]['name']}，你怎么看这个想法？")
    assert next(call for call in calls if not call["ctx"]["observer"])["ctx"]["bot_name"] == bots[2]["name"]


def test_proactivity_affects_initial_opportunity_without_a_fixed_leader(client, room, monkeypatch):
    cid, bots = room
    client.patch(f"/api/bots/{bots[1]['id']}", json={"proactivity": 0.1})
    client.patch(f"/api/bots/{bots[2]['id']}", json={"proactivity": 0.9})
    calls = model(monkeypatch)
    result = send(client, cid, "我们聊聊这个方案")
    assert {call["ctx"]["bot_name"] for call in calls[:2]} == {bots[0]["name"], bots[2]["name"]}
    assert calls[-1]["ctx"]["bot_name"] == bots[1]["name"]
    with SessionLocal() as db:
        assert db.get(Run, result["run"]["id"]).config_snapshot["free"]["primary_bot_id"] == bots[2]["id"]


def test_empty_agreement_is_silent_but_a_substantive_objection_is_published(client, room, monkeypatch):
    cid, bots = room
    model(monkeypatch, {bots[0]["name"]: "可以先做试点。", bots[1]["name"]: "我同意上面的观点。",
                        bots[2]["name"]: "但这个预算没有覆盖维护成本。"})
    send(client, cid, "你们怎么看这个方案")
    assert [m["sender_bot_id"] for m in answers(client, cid)] == [bots[0]["id"], bots[2]["id"]]


def test_simple_votes_are_preserved_when_the_user_asks_for_them(client, room, monkeypatch):
    cid, bots = room
    model(monkeypatch, {bots[0]["name"]: "可以先试点。", bots[1]["name"]: "支持。"})
    send(client, cid, "你们支持先做试点吗？")
    rows = answers(client, cid)
    assert len(rows) == 2
    assert {m["content"] for m in rows} == {"可以先试点。", "支持。"}


def test_questions_in_quotes_and_code_do_not_reopen_the_discussion(client, room, monkeypatch):
    cid, bots = room
    calls = model(monkeypatch, {bots[1]["name"]: f"只记录一下例子。\n> 大家怎么看？\n`@{bots[2]['name']} 请你回答？`"})
    send(client, cid, f"@{bots[1]['name']} 记录个例子")
    assert len(calls) == 1


def test_an_invitation_does_not_dispatch_every_member_mentioned_on_the_line(client, room):
    from app.agent.free_chat import handoff_members
    from app.core.models import Bot
    cid, bots = room
    with SessionLocal() as db:
        members = list(db.scalars(select(Bot).where(Bot.id.in_([bot["id"] for bot in bots]))))
        text = f"@{bots[1]['name']} 请核实一下，沿用 @{bots[2]['name']} 的观点。"
        assert handoff_members(text, members) == [bots[1]["id"]]
        text = f"先沿用 @{bots[2]['name']} 的观点，另外 @{bots[2]['name']} 请核对一下成本。"
        assert handoff_members(text, members) == [bots[2]["id"]]


def test_slow_primary_does_not_block_other_members_or_the_next_reply(client, room, monkeypatch):
    cid, bots = room

    async def verify():
        primary_started = asyncio.Event()
        release_primary = asyncio.Event()
        contexts = {}

        async def stream(**kw):
            name = kw["ctx"]["bot_name"]
            contexts[name] = json.dumps(kw["messages"], ensure_ascii=False)
            if name == bots[0]["name"]:
                primary_started.set()
                await release_primary.wait()
            else:
                await asyncio.wait_for(primary_started.wait(), 2)
            yield {"type": "done", "content": name + "独立的观点", "tool_calls": []}

        monkeypatch.setattr(orchestrator, "stream_chat", stream)
        rid = client.post(f"/api/chats/{cid}/messages", json={"content": "聊聊这个想法"}).json()["run"]["id"]
        await process_one(rid)
        with SessionLocal() as db:
            children = list(db.scalars(select(Run).where(Run.parent_run_id == rid)))
            primary = next(c.id for c in children if c.target_bot_id == bots[0]["id"])
            other = next(c.id for c in children if c.target_bot_id == bots[1]["id"])
        slow = asyncio.create_task(process_one(primary))
        try:
            await asyncio.wait_for(process_one(other), 3)
            assert not slow.done()
            assert [m["sender_bot_id"] for m in answers(client, cid) if m["content"]] == [bots[1]["id"]]
            assert rid in _wake_waiting_parents() or _root_status(rid) == "queued"
            await process_one(rid)
            with SessionLocal() as db:
                third = db.scalar(select(Run.id).where(Run.parent_run_id == rid, Run.target_bot_id == bots[2]["id"]))
            assert third, "已有成员回复时应继续接话，不能等慢成员收尾"
            await asyncio.wait_for(process_one(third), 3)
            assert not slow.done()
            assert bots[1]["name"] + "独立的观点" in contexts[bots[2]["name"]]
            assert {m["sender_bot_id"] for m in answers(client, cid) if m["content"]} == {bots[1]["id"], bots[2]["id"]}
        finally:
            release_primary.set()
            await slow
        assert await drain(rid) == "completed"
        assert len(answers(client, cid)) == 3

    asyncio.run(verify())


def test_latest_reply_is_not_chosen_by_child_run_creation_order(client, room, monkeypatch):
    from datetime import timedelta

    cid, bots = room
    model(monkeypatch, {b["name"]: b["name"] + "补充一条独立事实。" for b in bots})

    async def verify():
        rid = client.post(f"/api/chats/{cid}/messages", json={"content": "聊聊方案"}).json()["run"]["id"]
        await process_one(rid)
        with SessionLocal() as db:
            children = list(db.scalars(select(Run).where(Run.parent_run_id == rid)))
            primary = next(c.id for c in children if c.target_bot_id == bots[0]["id"])
            other = next(c.id for c in children if c.target_bot_id == bots[1]["id"])
        await process_one(primary)
        await process_one(other)
        with SessionLocal() as db:
            db.get(Run, primary).created_at = db.get(Run, other).created_at + timedelta(seconds=1)
            db.commit()
        assert await drain(rid) == "completed"

    asyncio.run(verify())
    rows = answers(client, cid)
    latest_initial = next(m for m in rows if m["sender_bot_id"] == bots[1]["id"])
    supplement = next(m for m in rows if m["sender_bot_id"] == bots[2]["id"])
    assert supplement["meta"]["reply_to"] == latest_initial["id"]


def _root_status(rid):
    with SessionLocal() as db:
        return db.get(Run, rid).status


def test_six_members_get_initial_opportunities_instead_of_a_fixed_two(client, room, monkeypatch):
    from collections import Counter

    cid, bots = room
    provider = client.get(f"/api/bots/{bots[0]['id']}").json()["provider_id"]
    for number in range(3):
        bot = client.post("/api/bots", json={"name": f"群友{number}", "provider_id": provider, "model": "botly-mock"}).json()
        assert client.post(f"/api/chats/{cid}/members", json={"bot_ids": [bot["id"]]}).status_code == 200
        bots.append(bot)
    client.patch(f"/api/chats/{cid}", json={"max_speakers": 2})
    calls = model(monkeypatch, {b["name"]: b["name"] + "有个想法" for b in bots})
    for index in range(6):
        send(client, cid, f"聊聊话题{index}")
    participation = Counter(m["sender_bot_id"] for m in answers(client, cid))
    assert len(calls) == 12
    assert participation == Counter({bot["id"]: 2 for bot in bots})


def test_less_proactive_members_are_not_starved(client, room, monkeypatch):
    from collections import Counter

    cid, bots = room
    client.patch(f"/api/chats/{cid}", json={"max_speakers": 1})
    client.patch(f"/api/bots/{bots[1]['id']}", json={"proactivity": 0.1})
    client.patch(f"/api/bots/{bots[2]['id']}", json={"proactivity": 0.9})
    model(monkeypatch)
    for index in range(12):
        send(client, cid, f"聊聊不同的话题{index}")
    counts = Counter(m["sender_bot_id"] for m in answers(client, cid))
    assert counts[bots[1]["id"]] > 0
    assert counts[bots[2]["id"]] > counts[bots[1]["id"]]


def test_group_greeting_invites_more_than_the_usual_interlocutor(client, room, monkeypatch):
    cid, bots = room
    calls = model(monkeypatch, {bots[1]["name"]: "嗨，我今天刚试了个有意思的东西。"})
    send(client, cid, "大家好！")
    assert len(answers(client, cid)) == 2
    initial = next(c for c in calls if c["ctx"]["observer"])
    system = initial["messages"][0]["content"]
    assert "不用等其他人说完" in system
    assert "只接最近一句" not in system


def test_silent_model_times_out_and_releases_the_only_speaker_slot(client, room, monkeypatch):
    cid, bots = room
    client.patch(f"/api/chats/{cid}", json={"max_speakers": 1})
    monkeypatch.setattr(orchestrator, "FREE_CHAT_FIRST_RESPONSE_TIMEOUT", 0.05)
    closed = []
    calls = []

    async def stream(**kw):
        name = kw["ctx"]["bot_name"]
        calls.append(kw)
        if name == bots[0]["name"]:
            try:
                await asyncio.Event().wait()
            finally:
                closed.append(name)
        yield {"type": "done", "content": "换我来说说我的看法。", "tool_calls": []}

    monkeypatch.setattr(orchestrator, "stream_chat", stream)
    result = send(client, cid, "你好")
    assert closed == [bots[0]["name"]]
    assert len(calls) == 2 and all(c["max_retries"] == 0 for c in calls)
    assert [m["sender_bot_id"] for m in answers(client, cid)] == [bots[1]["id"]]
    with SessionLocal() as db:
        slow = db.scalar(select(Run).where(Run.parent_run_id == result["run"]["id"], Run.target_bot_id == bots[0]["id"]))
        assert slow.status == "failed" and "超时" in slow.end_reason


def test_optional_slow_stream_has_a_total_time_limit(client, room, monkeypatch):
    cid, bots = room
    monkeypatch.setattr(orchestrator, "FREE_CHAT_REPLY_TIMEOUT", 0.05)
    closed = []

    async def stream(**kw):
        name = kw["ctx"]["bot_name"]
        if kw["ctx"]["observer"]:
            try:
                while True:
                    yield {"type": "delta", "text": "还在生成"}
                    await asyncio.sleep(0.01)
            finally:
                closed.append(name)
        else:
            yield {"type": "done", "content": "我先给一个具体的看法。", "tool_calls": []}

    monkeypatch.setattr(orchestrator, "stream_chat", stream)
    send(client, cid, "聊聊这个点子")
    assert set(closed) == {b["name"] for b in bots[1:]}
    assert len(answers(client, cid)) == 1


def test_silent_observer_can_take_over_after_the_primary_fails(client, room, monkeypatch):
    from app.agent.llm import LLMError

    cid, bots = room
    attempts = []

    async def stream(**kw):
        name = kw["ctx"]["bot_name"]
        attempts.append((name, kw["ctx"]["observer"]))
        if name == bots[0]["name"]:
            raise LLMError("这个成员的服务暂时不可用")
        text = "[PASS]" if kw["ctx"]["observer"] else "我来接一下这个问题。"
        yield {"type": "done", "content": text, "tool_calls": []}

    monkeypatch.setattr(orchestrator, "stream_chat", stream)

    async def verify():
        rid = client.post(f"/api/chats/{cid}/messages", json={"content": "聊聊这个点子"}).json()["run"]["id"]
        await process_one(rid)
        with SessionLocal() as db:
            children = list(db.scalars(select(Run).where(Run.parent_run_id == rid)))
            primary = next(c.id for c in children if c.target_bot_id == bots[0]["id"])
            other = next(c.id for c in children if c.target_bot_id == bots[1]["id"])
        await process_one(other)
        await process_one(rid)
        with SessionLocal() as db:
            third = db.scalar(select(Run.id).where(Run.parent_run_id == rid, Run.target_bot_id == bots[2]["id"]))
        await process_one(third)
        await process_one(primary)
        assert await drain(rid) == "completed"

    asyncio.run(verify())
    assert [m["sender_bot_id"] for m in answers(client, cid)] == [bots[1]["id"]]
    assert (bots[1]["name"], False) in attempts
    assert len(attempts) <= 4, "兜底只重试尚未承担主回复的成员，不能循环轮询"
