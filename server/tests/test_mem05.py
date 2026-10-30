"""MEM-05 长对话摘要与截断的回归测试。"""
from __future__ import annotations

import asyncio

from sqlalchemy import func, select

from app import orchestrator
from app.db import SessionLocal
from app.models import Bot, Chat, Message, Run, Thread
from app.orchestrator import RunExecutor
from tests.conftest import register, setup_workspace


def _seed_history(client, invite, email: str) -> tuple[str, str]:
    register(client, invite, email)
    chat_id, bots = setup_workspace(client)
    r = client.post(f"/api/chats/{chat_id}/messages", json={"content": "触发一次执行以生成 run"})
    assert r.status_code == 201, r.text
    run_id = r.json()["run"]["id"]

    with SessionLocal() as db:
        chat = db.get(Chat, chat_id)
        thread = db.scalar(select(Thread).where(Thread.chat_id == chat_id, Thread.type == "main"))
        bot_id = bots[0]["id"]
        base = db.scalar(select(func.max(Message.seq)).where(Message.thread_id == thread.id)) or 0
        for i in range(50):
            db.add(
                Message(
                    user_id=chat.user_id,
                    thread_id=thread.id,
                    seq=base + 1 + i,
                    sender_type="user" if i % 2 == 0 else "bot",
                    sender_bot_id=None if i % 2 == 0 else bot_id,
                    type="text",
                    content=f"第 {i} 条历史记录，关键事实：增长率为 {i * 7}%，决策是接入成本优先。",
                    status="done",
                )
            )
        db.commit()
    return chat_id, run_id


def test_mem05_summarize_and_inject(client, invite, monkeypatch):
    chat_id, run_id = _seed_history(client, invite, "mem05@test.dev")
    monkeypatch.setattr(orchestrator, "SUMMARY_THRESHOLD_TOKENS", 50)

    with SessionLocal() as db:
        run = db.get(Run, run_id)
        chat = db.get(Chat, chat_id)
        thread = db.scalar(select(Thread).where(Thread.chat_id == chat_id, Thread.type == "main"))
        assert thread.summary is None

        asyncio.run(
            RunExecutor(run_id)._maybe_summarize(
                db, run, thread, kind="mock", base_url="", api_key=None, model="botly-mock"
            )
        )
        assert thread.summary, "应该生成摘要"
        assert thread.summary_upto_seq > 0

        # 上下文里注入摘要、历史只保留最近 40 条原文
        sup = db.get(Bot, chat.supervisor_bot_id)
        built = RunExecutor(run_id)._build_messages(db, run, chat, thread, sup, True, [sup])
        summary_blocks = [
            m for m in built
            if m.get("role") == "assistant" and "更早对话的自动摘要" in str(m.get("content"))
        ]
        assert summary_blocks, "构造上下文时应注入摘要块"
        assert summary_blocks[0]["content"].strip().endswith(thread.summary.strip())


def test_mem05_below_threshold_does_not_summarize(client, invite, monkeypatch):
    chat_id, run_id = _seed_history(client, invite, "mem05-under@test.dev")
    # 阈值设得很大，未摘要部分不超阈值，不应生成摘要
    monkeypatch.setattr(orchestrator, "SUMMARY_THRESHOLD_TOKENS", 1_000_000)

    with SessionLocal() as db:
        run = db.get(Run, run_id)
        thread = db.scalar(select(Thread).where(Thread.chat_id == chat_id, Thread.type == "main"))
        asyncio.run(
            RunExecutor(run_id)._maybe_summarize(
                db, run, thread, kind="mock", base_url="", api_key=None, model="botly-mock"
            )
        )
        assert thread.summary is None
