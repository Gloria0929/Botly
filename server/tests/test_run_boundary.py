"""中断/结束一轮后，新消息不能变成补做上一轮任务的授权。"""
import asyncio
import json

import pytest
from sqlalchemy import select

from app import orchestrator
from app.db import SessionLocal
from app.models import Chat, Run, Thread, now
from app.orchestrator import add_message
from app.worker import process_one
from tests.conftest import register, setup_workspace


OLD_REQUEST = "搜索一下 weknora 并给我详细说明作用"


def previous_execution(client, invite, email, status):
    register(client, invite, email)
    chat_id, bots = setup_workspace(client)
    old_id = client.post(f"/api/chats/{chat_id}/messages", json={"content": OLD_REQUEST}).json()["run"]["id"]
    with SessionLocal() as db:
        run = db.get(Run, old_id)
        run.status = status
        run.ended_at = now()
        run.end_reason = "模型调用失败：请求超时" if status == "failed" else "用户停止" if status == "stopped" else "已完成"
        chat = db.get(Chat, chat_id)
        main = db.scalar(select(Thread).where(Thread.chat_id == chat_id, Thread.type == "main"))
        add_message(db, thread=main, run_id=old_id, sender_type="bot", sender_bot_id=chat.supervisor_bot_id,
                    type_="text", content="正在查找 WeKnora，尚未完成…", status="interrupted")
        add_message(db, thread=main, run_id=old_id, sender_type="bot", sender_bot_id=chat.supervisor_bot_id,
                    type_="tool_call", meta={"tool_calls": [{"id": "old-open-search", "type": "function", "function": {"name": "web_search", "arguments": '{"query":"WeKnora"}'}}]})
        add_message(db, thread=main, run_id=old_id, sender_type="system", type_="system", content=run.end_reason)
        db.commit()
    return chat_id, bots


@pytest.mark.parametrize("old_status", ["failed", "stopped", "completed"])
@pytest.mark.parametrize("new_request", ["你好", "只解释 Python 列表推导式，不要检索"])
def test_new_request_does_not_reopen_previous_task(client, invite, monkeypatch, old_status, new_request):
    label = "greeting" if new_request == "你好" else "new-task"
    chat_id, _ = previous_execution(client, invite, f"boundary-{old_status}-{label}@test.dev", old_status)
    seen = []

    async def respond(**kw):
        seen.append(kw)
        assert kw["ctx"]["role"] == "supervisor", "新请求不应执行上一轮子任务"
        current = [m["content"] for m in kw["messages"] if m["role"] == "user"]
        assert current == [new_request], "历史请求不得继续作为可执行的 user 消息"
        reference = next(m for m in kw["messages"] if "历史对话参考资料" in m["content"])
        assert reference["role"] == "assistant"
        records = json.loads(reference["content"].split("\n", 1)[1])
        assert any(r["content"] == OLD_REQUEST and r["execution_status"] == old_status for r in records)
        assert all(m.get("role") != "tool" and not m.get("tool_calls") for m in kw["messages"])
        yield {"type": "done", "content": "你好！有什么可以帮你？" if new_request == "你好" else "列表推导式用于构造列表。", "tool_calls": []}

    monkeypatch.setattr(orchestrator, "stream_chat", respond)
    new_id = client.post(f"/api/chats/{chat_id}/messages", json={"content": new_request}).json()["run"]["id"]
    asyncio.run(process_one(new_id))
    result = client.get(f"/api/runs/{new_id}").json()
    assert result["status"] == "completed" and len(seen) == 1
    assert client.get(f"/api/chats/{chat_id}").json()["tasks"] == []
    messages = client.get(f"/api/chats/{chat_id}/messages?limit=200").json()["messages"]
    assert not [m for m in messages if m["run_id"] == new_id and m["type"] == "tool_call"]


def test_explicit_continuation_can_reference_failed_task(client, invite, monkeypatch):
    chat_id, bots = previous_execution(client, invite, "boundary-explicit-continue@test.dev", "failed")
    worker = next(b for b in bots if b["name"] == "研究员")
    current_request = "继续刚才的 WeKnora 检索"
    supervisor_calls = 0

    async def respond(**kw):
        nonlocal supervisor_calls
        if kw["ctx"]["role"] == "worker":
            assert "WeKnora" in kw["ctx"]["task"]
            yield {"type": "done", "content": "检索完成", "tool_calls": []}
            return
        supervisor_calls += 1
        assert [m["content"] for m in kw["messages"] if m["role"] == "user"] == [current_request]
        assert any(OLD_REQUEST in m["content"] and "参考资料" in m["content"] for m in kw["messages"])
        if supervisor_calls == 1:
            yield {"type": "done", "content": "", "tool_calls": [{"id": "explicit-continue", "type": "function", "function": {
                "name": "delegate", "arguments": json.dumps({"bot": worker["name"], "instruction": "继续检索 WeKnora 官方资料"}),
            }}]}
        else:
            yield {"type": "done", "content": "已按要求继续检索", "tool_calls": []}

    monkeypatch.setattr(orchestrator, "stream_chat", respond)
    new_id = client.post(f"/api/chats/{chat_id}/messages", json={"content": current_request}).json()["run"]["id"]
    asyncio.run(process_one(new_id))
    assert client.get(f"/api/runs/{new_id}").json()["status"] == "completed"
    tasks = client.get(f"/api/chats/{chat_id}").json()["tasks"]
    assert len(tasks) == 1 and tasks[0]["status"] == "completed"
