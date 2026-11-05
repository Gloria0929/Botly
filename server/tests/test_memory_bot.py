"""MEM-03 修订：Bot 自主记忆（memory_save）与子 Agent 记忆候选。

- memory_save 只能写 Bot 私有记忆，去重、内容限长、每次执行最多 3 条；
- tools_for_bot 对所有 Bot 下发 memory_save（与 ask_user 同级）；
- 记忆候选的归属跟随执行者：@ 直连 / 子执行 → Bot 私有；总指挥 → 共享。
"""
from __future__ import annotations

import asyncio

from app.core.db import SessionLocal
from app.core.models import Bot, Chat, Memory, MemoryCandidate, Run
from app.agent.orchestrator import RunExecutor, ToolCtx
from app.agent.summary_memory import _memory_scope_for
from app.tools.registry import MEMORY_TOOL, _memory_save, tools_for_bot
from tests.conftest import register, setup_workspace


def _ctx_for(run_id: str) -> ToolCtx:
    with SessionLocal() as db:
        run = db.get(Run, run_id)
        chat = db.get(Chat, run.chat_id)
        bot = db.query(Bot).filter(Bot.user_id == run.user_id).first()
        return ToolCtx(db, run, chat, bot, None)


def _bot_rows(user_id: str, bot_id: str) -> list[Memory]:
    with SessionLocal() as db:
        return (
            db.query(Memory)
            .filter(Memory.user_id == user_id, Memory.scope == "bot", Memory.bot_id == bot_id)
            .all()
        )


def test_memory_save_writes_private_memory(client, invite):
    register(client, invite, "membot@test.dev")
    chat_id, _bots = setup_workspace(client)
    r = client.post(f"/api/chats/{chat_id}/messages", json={"content": "记住我用 pytest"})
    run_id = r.json()["run"]["id"]

    ctx = _ctx_for(run_id)
    content = "用户偏好简洁的中文表达，不要使用感叹号。"
    out = asyncio.run(_memory_save(ctx, content=content))
    ctx.db.commit()
    assert out.startswith("已保存到你的私有记忆")

    rows = _bot_rows(ctx.user_id, ctx.bot.id)
    assert len(rows) == 1
    assert rows[0].source == "bot"
    assert rows[0].run_id == run_id
    assert rows[0].content == content

    # 重复内容去重
    out2 = asyncio.run(_memory_save(ctx, content=content))
    ctx.db.commit()
    assert "无需重复保存" in out2

    # 内容太短直接拒绝
    assert "太短" in asyncio.run(_memory_save(ctx, content="太短了"))

    # 超长截断到 500
    long_out = asyncio.run(_memory_save(ctx, content="长" * 600))
    ctx.db.commit()
    assert "已保存" in long_out
    saved = (
        SessionLocal()
        .query(Memory)
        .filter(Memory.run_id == run_id, Memory.source == "bot")
        .all()
    )
    assert len(saved) == 2
    assert max(len(m.content) for m in saved) == 500


def test_memory_save_run_limit(client, invite):
    register(client, invite, "membot-limit@test.dev")
    chat_id, _bots = setup_workspace(client)
    r = client.post(f"/api/chats/{chat_id}/messages", json={"content": "开始干活"})
    run_id = r.json()["run"]["id"]

    ctx = _ctx_for(run_id)
    try:
        for content in ("用户偏好交付前状态全部回归测试。", "项目部署使用 Postgres 数据库。", "每周五需要发送项目进度报告。"):
            out = asyncio.run(_memory_save(ctx, content=content))
            assert out.startswith("已保存")
        out4 = asyncio.run(_memory_save(ctx, content="用户约定第 4 条：文档放 docs 目录。"))
        ctx.db.commit()
        assert "最多自动保存 3 条" in out4
    finally:
        ctx.db.close()
    count = (
        SessionLocal()
        .query(Memory)
        .filter(Memory.run_id == run_id, Memory.source == "bot")
        .count()
    )
    assert count == 3


def test_memory_tool_available_to_every_bot():
    names = {t.name for t in tools_for_bot(["web_read"], is_supervisor=False)}
    assert "memory_save" in names
    assert MEMORY_TOOL.level == "L1"


def test_memory_scope_follows_executor(client, invite):
    register(client, invite, "membot-scope@test.dev")
    chat_id, bots = setup_workspace(client)
    member = bots[-1]["id"]
    r = client.post(f"/api/chats/{chat_id}/messages", json={"content": "查一下天气"})
    run_id = r.json()["run"]["id"]

    with SessionLocal() as db:
        run = db.get(Run, run_id)
        chat = db.get(Chat, run.chat_id)
        # 默认：总指挥执行 → 共享
        assert _memory_scope_for(db, run, chat) == ("shared", None)
        # @ 直连：执行者私有
        run.target_bot_id = member
        db.commit()

    with SessionLocal() as db:
        run = db.get(Run, run_id)
        chat = db.get(Chat, run.chat_id)
        assert _memory_scope_for(db, run, chat) == ("bot", member)


def test_direct_run_candidates_use_bot_scope(client, invite, monkeypatch):
    """@ 直连执行（执行者不是总指挥）生成的候选归属该 Bot 的私有记忆。"""
    register(client, invite, "membot-cand@test.dev")
    chat_id, bots = setup_workspace(client)
    member = bots[-1]["id"]
    r = client.post(
        f"/api/chats/{chat_id}/messages",
        json={"content": "以后这类任务都输出 Markdown 表格", "mention_bot_id": member},
    )
    assert r.status_code == 201, r.text
    run_id = r.json()["run"]["id"]

    async def fake_complete(self, **kwargs):
        return '[{"title":"输出格式","content":"这类任务用户要求输出 Markdown 表格"}]'

    monkeypatch.setattr(RunExecutor, "_complete_text", fake_complete)
    with SessionLocal() as db:
        run = db.get(Run, run_id)
        run.status = "completed"
        db.commit()

    asyncio.run(RunExecutor(run_id)._generate_memory_candidates("已完成"))

    with SessionLocal() as db:
        cands = (
            db.query(MemoryCandidate)
            .filter(MemoryCandidate.run_id == run_id)
            .all()
        )
        assert cands and cands[0].scope == "bot" and cands[0].bot_id == member


def test_supervisor_run_candidates_stay_shared(client, invite, monkeypatch):
    """总指挥执行的候选仍是共享记忆（原有 MEM-04 行为不回退）。"""
    register(client, invite, "membot-sup@test.dev")
    chat_id, _bots = setup_workspace(client)
    r = client.post(f"/api/chats/{chat_id}/messages", json={"content": "以后回答尽量简洁"})
    run_id = r.json()["run"]["id"]

    async def fake_complete(self, **kwargs):
        return '[{"title":"表达偏好","content":"用户偏好简洁的中文表达"}]'

    monkeypatch.setattr(RunExecutor, "_complete_text", fake_complete)
    with SessionLocal() as db:
        db.get(Run, run_id).status = "completed"
        db.commit()

    asyncio.run(RunExecutor(run_id)._generate_memory_candidates("已完成"))

    with SessionLocal() as db:
        cands = (
            db.query(MemoryCandidate)
            .filter(MemoryCandidate.run_id == run_id)
            .all()
        )
        assert cands and cands[0].scope == "shared" and cands[0].bot_id is None
