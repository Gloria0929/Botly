"""工具调用只留在任务线程（任务面板），群聊只展示 bot 的聊天记录。"""
from __future__ import annotations

import asyncio

from sqlalchemy import select

from app.core.db import SessionLocal
from app.core.models import Message, Run, Task, Thread
from app.agent.orchestrator import RunExecutor
from tests.conftest import register, setup_workspace


def _setup(client, invite, email):
    register(client, invite, email)
    chat_id, bots = setup_workspace(client)
    run_id = client.post(
        f"/api/chats/{chat_id}/messages", json={"content": "帮我整理资料"}
    ).json()["run"]["id"]
    worker = next(b for b in bots if b["name"] == "研究员")
    with SessionLocal() as db:
        run = db.get(Run, run_id)
        main = db.scalar(select(Thread).where(Thread.chat_id == chat_id, Thread.type == "main"))
        thread = Thread(
            user_id=run.user_id,
            chat_id=chat_id,
            parent_thread_id=main.id,
            type="task",
            title="资料收集",
            bot_id=worker["id"],
        )
        db.add(thread)
        db.flush()
        task = Task(
            user_id=run.user_id,
            run_id=run_id,
            thread_id=thread.id,
            assigned_bot_id=worker["id"],
            instruction="资料收集",
            status="running",
        )
        db.add(task)
        db.commit()
        return {
            "chat_id": chat_id,
            "run_id": run_id,
            "main_id": main.id,
            "task_id": task.id,
            "task_thread_id": thread.id,
            "worker_id": worker["id"],
        }


def _results(thread_id: str, task_id: str | None = None) -> list[Message]:
    with SessionLocal() as db:
        rows = db.scalars(
            select(Message).where(
                Message.thread_id == thread_id, Message.type == "tool_result"
            )
        ).all()
        return [m for m in rows if task_id is None or (m.meta or {}).get("task_id") == task_id]


def test_tool_result_stays_in_task_thread(client, invite):
    ctx = _setup(client, invite, "mirror@test.dev")
    ex = RunExecutor(ctx["run_id"])
    call = {"id": "call-1", "type": "function", "function": {"name": "file_write", "arguments": "{}"}}

    ex.write_tool_result(ctx["task_thread_id"], call, "已写入 report.md")
    # 节点重放/重复调用不应在任务线程多出第二条
    ex.write_tool_result(ctx["task_thread_id"], call, "已写入 report.md")

    task_rows = _results(ctx["task_thread_id"])
    assert len(task_rows) == 1 and task_rows[0].content == "已写入 report.md"
    # 群聊不再镜像工具返回：那边只保留聊天记录
    assert _results(ctx["main_id"], ctx["task_id"]) == []


def test_builtin_tool_result_stays_in_task_thread(client, invite):
    """真实执行一次内置工具：结果只落在任务线程，任务面板能读到。"""
    ctx = _setup(client, invite, "mirror-builtin@test.dev")
    ex = RunExecutor(ctx["run_id"])
    call = {"id": "call-list", "type": "function", "function": {"name": "file_list", "arguments": "{}"}}

    out = asyncio.run(
        ex.execute_tool(
            thread_id=ctx["task_thread_id"],
            bot_id=ctx["worker_id"],
            task_id=ctx["task_id"],
            call=call,
            role="worker",
        )
    )
    ex.write_tool_result(ctx["task_thread_id"], call, out)

    task_rows = _results(ctx["task_thread_id"])
    assert len(task_rows) == 1 and task_rows[0].content == out
    assert task_rows[0].meta["tool"] == "file_list"
    assert _results(ctx["main_id"], ctx["task_id"]) == []


def test_error_tool_result_stays_in_task_thread(client, invite):
    """未授权/失败的返回同样只落在任务线程，不污染群聊。"""
    ctx = _setup(client, invite, "mirror-error@test.dev")
    ex = RunExecutor(ctx["run_id"])
    call = {"id": "call-denied", "type": "function",
            "function": {"name": "file_delete", "arguments": "{}"}}

    out = asyncio.run(
        ex.execute_tool(
            thread_id=ctx["task_thread_id"],
            bot_id=ctx["worker_id"],
            task_id=ctx["task_id"],
            call=call,
            role="worker",
        )
    )
    ex.write_tool_result(ctx["task_thread_id"], call, out)

    task_rows = _results(ctx["task_thread_id"])
    assert len(task_rows) == 1 and task_rows[0].content == out
    assert _results(ctx["main_id"], ctx["task_id"]) == []
