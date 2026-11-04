"""流式片段及时提交，工具轮原位收尾，SSE 只在事件提交后被唤醒。"""
from __future__ import annotations

import asyncio
import threading

import pytest
from sqlalchemy import select

from app.agent import orchestrator
from app.agent.orchestrator import RunExecutor
from app.core import events
from app.core.db import SessionLocal
from app.core.models import Event, Message, Run, UsageLog
from app.worker import process_one
from tests.test_run_reliability import _new_run


@pytest.mark.parametrize("tool_count", [0, 1, 2])
def test_streamed_text_keeps_message_id_when_turn_finishes(client, invite, monkeypatch, tool_count):
    ctx = _new_run(client, invite, f"stream-stable-{tool_count}@test.dev")
    draft_ids = []
    calls = [{"id": f"tool-{i}", "type": "function", "function": {
        "name": "file_list", "arguments": "{}",
    }} for i in range(tool_count)]

    async def fake_stream_chat(**kw):
        with SessionLocal() as db:
            created = db.scalars(select(Event).where(Event.chat_id == ctx["chat_id"], Event.type == "message.created")).all()
            assert not any(e.payload.get("sender_type") == "bot" for e in created), "空草稿不能先广播"
        yield {"type": "delta", "text": "我先检查文件。"}
        with SessionLocal() as db:
            draft = db.scalar(select(Message).where(Message.run_id == ctx["run_id"], Message.status == "streaming"))
            draft_ids.append(draft.id)
            assert draft.content == "我先检查文件。"
        yield {"type": "done", "content": "我先检查文件。", "tool_calls": calls}

    monkeypatch.setattr(orchestrator, "stream_chat", fake_stream_chat)
    asyncio.run(RunExecutor(ctx["run_id"]).model_turn(
        role="supervisor", bot_id=ctx["supervisor_id"], thread_id=ctx["main_id"], task_id=None,
    ))
    with SessionLocal() as db:
        msg = db.get(Message, draft_ids[0])
        assert msg.content == "我先检查文件。" and msg.status == "done"
        assert msg.type == ("tool_call" if calls else "text")
        deleted = db.scalars(select(Event).where(Event.chat_id == ctx["chat_id"], Event.type == "message.deleted")).all()
        assert not any(e.payload["id"] == msg.id for e in deleted)
        completed = db.scalars(select(Event).where(Event.chat_id == ctx["chat_id"], Event.type == "message.completed")).all()
        assert any(e.payload["message_id"] == msg.id and e.payload["type"] == msg.type for e in completed)
        tools = db.scalars(select(Message).where(Message.run_id == ctx["run_id"], Message.type == "tool_call")).all()
        assert len(tools) == tool_count


def test_throttled_tail_is_flushed_while_model_is_still_streaming(client, invite, monkeypatch):
    ctx = _new_run(client, invite, "stream-tail@test.dev")

    async def fake_stream_chat(**kw):
        yield {"type": "delta", "text": "第一段"}
        yield {"type": "delta", "text": "第二段"}
        await asyncio.sleep(0.35)
        with SessionLocal() as db:
            draft = db.scalar(select(Message).where(Message.run_id == ctx["run_id"], Message.status == "streaming"))
            assert draft.content == "第一段", "草稿内容应低频落库"
            delta = db.scalar(select(Event).where(Event.chat_id == ctx["chat_id"], Event.type == "message.delta").order_by(Event.id.desc()))
            assert delta.payload["delta"] == "第二段" and delta.payload["offset"] == len("第一段")
            assert "content" not in delta.payload
        snapshot = client.get(f"/api/chats/{ctx['chat_id']}/messages").json()
        assert next(m for m in snapshot["messages"] if m["id"] == draft.id)["content"] == "第一段第二段"
        yield {"type": "done", "content": "第一段第二段", "tool_calls": []}

    monkeypatch.setattr(orchestrator, "stream_chat", fake_stream_chat)
    asyncio.run(RunExecutor(ctx["run_id"]).model_turn(
        role="supervisor", bot_id=ctx["supervisor_id"], thread_id=ctx["main_id"], task_id=None,
    ))


def test_sse_wakes_after_commit_and_never_for_rollback(client, invite, monkeypatch):
    ctx = _new_run(client, invite, "stream-wakeup@test.dev")
    published = []
    delivered = threading.Event()

    def capture(user_id, chat_id, event_id, type_):
        with SessionLocal() as reader:
            assert reader.get(Event, event_id), "唤醒 SSE 时事件必须对其他连接可见"
        published.append(event_id)
        delivered.set()

    monkeypatch.setattr(events.redis_bus, "publish_event", capture)
    with SessionLocal() as db:
        events.emit(db, ctx["user_id"], ctx["chat_id"], "message.delta", {"content": "第一段"})
        last = events.emit(db, ctx["user_id"], ctx["chat_id"], "message.delta", {"content": "第二段"})
        assert published == []
        db.commit()
        assert delivered.wait(1)
        assert published == [last.id]
        events.emit(db, ctx["user_id"], ctx["chat_id"], "message.delta", {"content": "回滚内容"})
        db.rollback()
        db.commit()
        assert published == [last.id]


def test_main_agent_usage_reaches_live_messages_and_historical_details(client, invite, monkeypatch):
    ctx = _new_run(client, invite, "main-agent-tokens@test.dev")
    turn = {"n": 0}

    async def fake_stream_chat(**kw):
        turn["n"] += 1
        if turn["n"] == 1:
            yield {"type": "delta", "text": "先查看文件。"}
            yield {"type": "done", "content": "先查看文件。", "usage": {"input_tokens": 120, "output_tokens": 30},
                   "tool_calls": [{"id": "tokens-list", "type": "function",
                                   "function": {"name": "file_list", "arguments": "{}"}}]}
        else:
            yield {"type": "delta", "text": "检查完成。"}
            yield {"type": "done", "content": "检查完成。", "usage": {"input_tokens": 220, "output_tokens": 40},
                   "tool_calls": []}

    monkeypatch.setattr(orchestrator, "stream_chat", fake_stream_chat)
    asyncio.run(process_one(ctx["run_id"]))
    with SessionLocal() as db:
        assert db.get(Run, ctx["run_id"]).status == "completed"
        replies = db.scalars(select(Message).where(
            Message.run_id == ctx["run_id"], Message.sender_type == "bot",
        ).order_by(Message.seq)).all()
        assert [m.meta["agent_tokens"] for m in replies] == [150, 410]
        usage_events = db.scalars(select(Event).where(
            Event.chat_id == ctx["chat_id"], Event.type == "usage.updated",
        ).order_by(Event.id)).all()
        assert [e.payload["agent_tokens"] for e in usage_events] == [150, 410]
        assert all(e.payload["bot_id"] == ctx["supervisor_id"] and e.payload["task_id"] is None for e in usage_events)
        # 旧消息没有展示元数据也能由用量日志补齐；其他 Bot 和子任务的用量不能混入。
        for m in replies:
            m.meta = {k: v for k, v in m.meta.items() if k != "agent_tokens"}
        db.add_all([
            UsageLog(user_id=ctx["user_id"], run_id=ctx["run_id"], bot_id=ctx["bots"]["研究员"]["id"], input_tokens=999, output_tokens=999),
            UsageLog(user_id=ctx["user_id"], run_id=ctx["run_id"], bot_id=ctx["supervisor_id"], task_id="child-task", input_tokens=888, output_tokens=888),
        ])
        db.commit()
    history = client.get(f"/api/chats/{ctx['chat_id']}/messages").json()["messages"]
    own = [m for m in history if m["sender_type"] == "bot" and m["run_id"] == ctx["run_id"]]
    assert len(own) == 2 and all(m["meta"]["agent_tokens"] == 410 for m in own)
