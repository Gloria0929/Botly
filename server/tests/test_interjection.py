"""RUN-13 执行中插话：子 Bot 应在新插话到达时提前交回总指挥，而不是跑完整轮。"""
from __future__ import annotations

import asyncio

from sqlalchemy import func, select

from app.db import SessionLocal
from app.models import Bot, Chat, Message, Run, Task, Thread
from app.orchestrator import RunExecutor
from tests.conftest import register, setup_workspace


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
            sender_type="user", type="text", content="先停一下，改个方向",
            meta={"kind": "interjection"}, status="done",
        ))
        db.commit()
        return run_id, worker["id"], task_thread.id, task.id


def test_worker_stops_on_new_interjection(client, invite):
    run_id, bot_id, thread_id, task_id = _make_interjection_run(
        client, invite, "interject-worker@test.dev"
    )
    ex = RunExecutor(run_id)
    result = asyncio.run(
        ex.model_turn(role="worker", bot_id=bot_id, thread_id=thread_id, task_id=task_id)
    )
    assert result[0] is False
    assert "补充了新要求" in result[1]


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
