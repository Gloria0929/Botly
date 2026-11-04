"""RUN-13 插话更新原任务上下文，串行、并行及收尾期间均不丢进度。"""
from __future__ import annotations

import asyncio
import json

import pytest

from sqlalchemy import func, select

from app.core.db import SessionLocal
from app.core.models import Bot, Chat, Message, Run, Task, Thread
from app.agent import orchestrator
from app.agent.orchestrator import RunExecutor, add_message
from tests.conftest import register, setup_workspace
from tests.test_parallel import drain_run, run_worker


def _make_interjection_run(client, invite, email: str) -> tuple[str, str, str, str]:
    register(client, invite, email)
    chat_id, bots = setup_workspace(client)
    r = client.post(f"/api/chats/{chat_id}/messages", json={"content": "调研并写一份报告"})
    assert r.status_code == 201, r.text
    run_id = r.json()["run"]["id"]
    worker = next(b for b in bots if b["name"] != "总指挥")  # 研究员

    with SessionLocal() as db:
        chat = db.get(Chat, chat_id)
        main = db.scalar(select(Thread).where(Thread.chat_id == chat_id, Thread.type == "main"))
        task_thread = Thread(
            user_id=chat.user_id, chat_id=chat_id, type="task",
            parent_thread_id=main.id, title="子任务", bot_id=worker["id"],
        )
        db.add(task_thread)
        db.flush()
        task = Task(
            user_id=chat.user_id, run_id=run_id, thread_id=task_thread.id,
            assigned_bot_id=worker["id"], instruction="收集资料", status="running",
        )
        db.add(task)
        db.flush()
        base = db.scalar(select(func.max(Message.seq)).where(Message.thread_id == main.id)) or 0
        db.add(Message(
            user_id=chat.user_id, thread_id=main.id, run_id=run_id, seq=base + 100,
            sender_type="user", type="text", content="补充：只看国内厂商，重点比较价格",
            meta={"kind": "interjection"}, status="done",
        ))
        db.commit()
        return run_id, worker["id"], task_thread.id, task.id


def test_worker_reads_interjection_without_aborting_task(client, invite, monkeypatch):
    run_id, bot_id, thread_id, task_id = _make_interjection_run(
        client, invite, "interject-worker@test.dev"
    )
    seen = []

    async def respond(**kw):
        seen.append(kw)
        assert kw["ctx"]["role"] == "worker"
        assert kw["messages"][-1] == {"role": "user", "content": "补充：只看国内厂商，重点比较价格"}
        assert "继续当前已分配的子任务" in kw["messages"][-2]["content"]
        yield {"type": "done", "content": "国内厂商价格已整理", "tool_calls": []}

    monkeypatch.setattr(orchestrator, "stream_chat", respond)
    ex = RunExecutor(run_id)
    result = asyncio.run(
        ex.model_turn(role="worker", bot_id=bot_id, thread_id=thread_id, task_id=task_id)
    )
    assert result == (False, "国内厂商价格已整理")
    assert len(seen) == 1
    with SessionLocal() as db:
        assert db.get(Task, task_id).status == "running"
        assert db.get(Run, run_id).last_interjection_seq == 0
        assert db.scalar(select(func.count(Task.id)).where(Task.run_id == run_id)) == 1
    assert not ex.has_pending_interjections(thread_id)


def test_pending_interjection_seq_and_mark_read(client, invite):
    run_id, bot_id, thread_id, task_id = _make_interjection_run(
        client, invite, "interject-worker-seq@test.dev"
    )
    with SessionLocal() as db:
        run = db.get(Run, run_id)
        main = db.scalar(
            select(Thread).where(Thread.chat_id == run.chat_id, Thread.type == "main")
        )
        ex = RunExecutor(run_id)
        assert ex._pending_interjection_seq(db, run, main) > 0
        assert (run.last_interjection_seq or 0) == 0
        ex._mark_interjections_read(db, run, main)
        assert run.last_interjection_seq > 0
        assert ex._pending_interjection_seq(db, run, main) == 0


def _call(name, args, call_id):
    return {"id": call_id, "type": "function", "function": {
        "name": name, "arguments": json.dumps(args, ensure_ascii=False),
    }}


@pytest.mark.parametrize("parallel", [False, True])
@pytest.mark.parametrize("timing", ["tool_round", "final_round"])
def test_interjection_continues_original_task_and_preserves_tool_results(
    client, invite, monkeypatch, parallel, timing,
):
    register(client, invite, f"interject-{parallel}-{timing}@test.dev")
    chat_id, bots = setup_workspace(client)
    worker_id = next(b["id"] for b in bots if b["name"] == "研究员")
    turns = {"supervisor": 0, "研究员": 0, "架构师": 0}
    original_task_id = None
    run_id = None
    update = "补充：重点比较价格，保留已经收集的资料"

    async def respond(**kw):
        nonlocal original_task_id
        if kw["ctx"]["role"] == "supervisor":
            turns["supervisor"] += 1
            if parallel and turns["supervisor"] == 1:
                yield {"type": "done", "content": "", "tool_calls": [_call("propose_plan", {"steps": [
                    {"title": "收集资料", "bot": "研究员", "depends_on": []},
                    {"title": "独立复核", "bot": "架构师", "depends_on": []},
                ]}, "interjection-plan")]}
            elif turns["supervisor"] == (2 if parallel else 1):
                yield {"type": "done", "content": "", "tool_calls": [_call("delegate", {
                    "bot": "研究员", "instruction": "收集资料", "step": "收集资料",
                }, "original-delegate")]}
            else:
                assert any(m["role"] == "user" and m["content"] == update for m in kw["messages"])
                assert any(m["role"] == "tool" and "价格对比已完成" in m["content"] for m in kw["messages"])
                yield {"type": "done", "content": "已汇总价格对比", "tool_calls": []}
            return

        name = kw["ctx"]["bot_name"]
        turns[name] += 1
        if name == "架构师":
            assert kw["messages"][-1] == {"role": "user", "content": update}
            yield {"type": "done", "content": "复核完成", "tool_calls": []}
            return
        with SessionLocal() as db:
            task = db.scalar(select(Task).where(Task.run_id == run_id, Task.assigned_bot_id == worker_id))
            assert task.status == "running"
            if original_task_id is None:
                original_task_id = task.id
            assert task.id == original_task_id
        if turns[name] == 1:
            assert update not in [m["content"] for m in kw["messages"] if m["role"] == "user"]
            yield {"type": "delta", "text": "已收集部分资料"}
            response = client.post(f"/api/chats/{chat_id}/messages", json={"content": update, "interject": True})
            assert response.status_code == 201, response.text
            assert response.json()["run"]["id"] == run_id
            calls = [_call("file_list", {}, "preserved-file-list")] if timing == "tool_round" else []
            yield {"type": "done", "content": "已收集部分资料", "tool_calls": calls}
        else:
            assert turns[name] == 2, "同一条补充不应使原任务反复重跑"
            assert kw["messages"][-1] == {"role": "user", "content": update}
            assert any(m["role"] == "assistant" and m["content"] == "已收集部分资料" for m in kw["messages"])
            if timing == "tool_round":
                assert any(m["role"] == "tool" and m["tool_call_id"] == "preserved-file-list" for m in kw["messages"])
            yield {"type": "done", "content": "价格对比已完成", "tool_calls": []}

    monkeypatch.setattr(orchestrator, "stream_chat", respond)
    run_id = client.post(f"/api/chats/{chat_id}/messages", json={"content": "调研厂商"}).json()["run"]["id"]
    if parallel:
        run_worker(run_id)
        assert client.post(f"/api/runs/{run_id}/plan/approve", json={}).status_code == 200
    result = drain_run(client, run_id)
    assert result["status"] == "completed", result
    with SessionLocal() as db:
        tasks = db.scalars(select(Task).where(Task.run_id == run_id)).all()
        assert len(tasks) == (2 if parallel else 1)
        assert all(t.status == "completed" for t in tasks)
        assert db.get(Task, original_task_id).result == "价格对比已完成"
        notes = [m for m in db.scalars(select(Message).where(Message.run_id == run_id))
                 if (m.meta or {}).get("delegation")]
        assert len(notes) == len(tasks), "插话不得生成第二次派发消息"


@pytest.mark.parametrize("nested", [False, True])
def test_parallel_and_nested_workers_read_root_updates_even_after_supervisor_read(
    client, invite, nested,
):
    run_id, bot_id, thread_id, task_id = _make_interjection_run(client, invite, f"root-updates-{nested}@test.dev")
    with SessionLocal() as db:
        root = db.get(Run, run_id)
        chat = db.get(Chat, root.chat_id)
        main = db.scalar(select(Thread).where(Thread.chat_id == chat.id, Thread.type == "main"))
        task = db.get(Task, task_id)
        worker = db.get(Bot, bot_id)
        child = Run(user_id=root.user_id, chat_id=root.chat_id, parent_run_id=root.id,
                    parent_task_id=task.id, target_bot_id=bot_id, thread_id=thread_id)
        db.add(child)
        db.flush()
        thread = db.get(Thread, thread_id)
        if nested:
            thread = Thread(user_id=root.user_id, chat_id=root.chat_id, type="task",
                            parent_thread_id=thread_id, bot_id=bot_id)
            db.add(thread)
            db.flush()
            db.add(Task(user_id=root.user_id, run_id=child.id, thread_id=thread.id,
                        assigned_bot_id=bot_id, instruction="复核价格", status="running"))
        ex = RunExecutor(child.id)
        ex._mark_interjections_read(db, root, main)
        add_message(db, thread=main, run_id="other-run", sender_type="user", type_="text",
                    content="另一轮的补充不得混入", meta={"kind": "interjection"})
        messages = ex._build_messages(db, child, chat, thread, worker, False, [worker])
        assert messages[-1]["content"] == "补充：只看国内厂商，重点比较价格"
        assert "另一轮的补充不得混入" not in [m["content"] for m in messages]


def test_supervisor_final_output_receives_late_interjection(client, invite, monkeypatch):
    register(client, invite, "supervisor-late-update@test.dev")
    chat_id, _ = setup_workspace(client)
    turns = 0
    run_id = None

    async def respond(**kw):
        nonlocal turns
        turns += 1
        if turns == 1:
            yield {"type": "delta", "text": "初步说明"}
            client.post(f"/api/chats/{chat_id}/messages", json={"content": "补充：再给一个示例", "interject": True})
            yield {"type": "done", "content": "初步说明", "tool_calls": []}
        else:
            assert turns == 2
            assert any(m["role"] == "user" and m["content"] == "补充：再给一个示例" for m in kw["messages"])
            yield {"type": "done", "content": "说明及示例", "tool_calls": []}

    monkeypatch.setattr(orchestrator, "stream_chat", respond)
    run_id = client.post(f"/api/chats/{chat_id}/messages", json={"content": "解释一下价格口径"}).json()["run"]["id"]
    run_worker(run_id)
    assert client.get(f"/api/runs/{run_id}").json()["status"] == "completed"
    assert turns == 2
