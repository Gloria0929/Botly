"""RUN-09：多层委派（子 Bot 再向下派任务）。

覆盖：
1. 默认两层：总指挥 → 研究员 → 写手，结果逐层交回；
2. 委派深度上限：达到上限后不再下发 delegate，模型硬发也会被拒绝；
3. 不能把任务派回总指挥或自己的上级；
4. 嵌套子任务失败只交回上级，不结束整个执行。
"""
from __future__ import annotations

import asyncio
import json

from sqlalchemy import select

from app.core.db import SessionLocal
from app.core.models import Message, Task, Thread
from app.worker import process_one
from tests.conftest import register, setup_workspace


def run_worker(run_id: str) -> None:
    asyncio.run(process_one(run_id))


def wait_status(client, run_id: str, expect: set[str]) -> dict:
    run = client.get(f"/api/runs/{run_id}").json()
    assert run["status"] in expect, f"期望 {expect}，实际 {run['status']}（{run.get('end_reason')}）"
    return run


def messages(client, chat_id: str) -> list[dict]:
    return client.get(f"/api/chats/{chat_id}/messages?limit=200").json()["messages"]


def _pick(bots: list[dict], name: str) -> dict:
    return next(b for b in bots if b["name"] == name)


def _tool_names(tools: list[dict]) -> set[str]:
    return {t["function"]["name"] for t in (tools or [])}


def _delegate_call(call_id: str, bot: str, instruction: str) -> dict:
    return {
        "id": call_id,
        "type": "function",
        "function": {
            "name": "delegate",
            "arguments": json.dumps({"bot": bot, "instruction": instruction}, ensure_ascii=False),
        },
    }


def test_nested_delegation_runs_two_levels(client, invite, monkeypatch):
    """总指挥 → 研究员 → 写手：两级子任务都要执行，结果逐层交回。"""
    from app.agent import orchestrator

    register(client, invite, "nested-two-levels@test.dev")
    chat_id, bots = setup_workspace(client)
    researcher = _pick(bots, "研究员")
    writer = _pick(bots, "写手")
    turns = {"supervisor": 0, "researcher": 0}

    async def fake_stream_chat(**kw):
        role = kw["ctx"]["role"]
        bot_name = kw["ctx"]["bot_name"]
        names = _tool_names(kw.get("tools") or [])
        if role == "supervisor":
            turns["supervisor"] += 1
            if turns["supervisor"] == 1:
                yield {
                    "type": "done",
                    "content": "",
                    "tool_calls": [_delegate_call("top-delegate", researcher["name"], "调研并整理要点")],
                }
                return
            assert any(
                m["role"] == "tool" and "研究员汇总" in m["content"] for m in kw["messages"]
            ), "总指挥必须拿到研究员的最终汇总"
            yield {"type": "done", "content": "全部完成", "tool_calls": []}
            return

        if bot_name == "研究员":
            turns["researcher"] += 1
            assert "delegate" in names, "第一层子 Bot 在深度允许时必须拿到 delegate"
            if turns["researcher"] == 1:
                yield {
                    "type": "done",
                    "content": "",
                    "tool_calls": [_delegate_call("nested-delegate", writer["name"], "根据要点写稿")],
                }
                return
            assert any(
                m["role"] == "tool" and "稿件完成" in m["content"] for m in kw["messages"]
            ), "研究员必须拿到写手交回的结果"
            yield {"type": "done", "content": "研究员汇总：稿件完成", "tool_calls": []}
            return

        # 写手：最底层，不应该再拿到 delegate
        assert bot_name == "写手"
        assert "delegate" not in names, "到达深度上限的成员不应再下发 delegate"
        yield {"type": "done", "content": "稿件完成：report.md", "tool_calls": []}

    monkeypatch.setattr(orchestrator, "stream_chat", fake_stream_chat)
    run_id = client.post(
        f"/api/chats/{chat_id}/messages", json={"content": "先调研再成稿"}
    ).json()["run"]["id"]
    run_worker(run_id)
    run = wait_status(client, run_id, {"completed"})

    with SessionLocal() as db:
        tasks = db.scalars(
            select(Task).where(Task.run_id == run_id).order_by(Task.created_at)
        ).all()
        assert len(tasks) == 2
        assert all(t.status == "completed" for t in tasks)
        outer, inner = tasks
        assert outer.assigned_bot_id == researcher["id"]
        assert inner.assigned_bot_id == writer["id"]
        outer_thread = db.get(Thread, outer.thread_id)
        inner_thread = db.get(Thread, inner.thread_id)
        assert outer_thread.parent_thread_id is not None
        assert inner_thread.parent_thread_id == outer.thread_id, "孙任务线程要挂在上级任务线程下"

        inner_result = db.scalars(
            select(Message).where(
                Message.thread_id == outer.thread_id,
                Message.type == "tool_result",
            )
        ).all()
        assert any("稿件完成" in (m.content or "") for m in inner_result), "结果要回到上级线程"

    msgs = messages(client, chat_id)
    notes = [m for m in msgs if (m["meta"] or {}).get("delegation")]
    assert len(notes) == 2
    nested_note = next(m for m in notes if (m["meta"] or {}).get("mention_name") == "写手")
    assert nested_note["sender_bot_id"] == researcher["id"], "嵌套派任务由上级成员发起"
    assert nested_note["content"].startswith("@写手")
    assert any(m["content"] == "研究员汇总：稿件完成" for m in msgs)
    assert any(m["content"] == "全部完成" for m in msgs)


def test_depth_limit_rejects_further_delegation(client, invite, monkeypatch):
    """委派深度 = 1 时，子 Bot 没有 delegate；即使硬发调用也被拒绝。"""
    from app.agent import orchestrator

    register(client, invite, "nested-depth-limit@test.dev")
    chat_id, bots = setup_workspace(client)
    researcher = _pick(bots, "研究员")
    writer = _pick(bots, "写手")
    assert client.patch(
        f"/api/chats/{chat_id}", json={"limits_override": {"delegation_depth": 1}}
    ).status_code == 200
    assert client.get(f"/api/chats/{chat_id}").json()["limits"]["delegation_depth"] == 1
    turns = {"supervisor": 0, "researcher": 0}

    async def fake_stream_chat(**kw):
        if kw["ctx"]["role"] == "supervisor":
            turns["supervisor"] += 1
            if turns["supervisor"] == 1:
                yield {
                    "type": "done",
                    "content": "",
                    "tool_calls": [_delegate_call("top-delegate", researcher["name"], "调研要点")],
                }
                return
            yield {"type": "done", "content": "完成", "tool_calls": []}
            return

        turns["researcher"] += 1
        assert "delegate" not in _tool_names(kw.get("tools") or [])
        if turns["researcher"] == 1:
            # 模拟模型硬发一个没下发的工具调用
            yield {
                "type": "done",
                "content": "",
                "tool_calls": [_delegate_call("illegal-delegate", writer["name"], "写稿")],
            }
            return
        assert any(
            m["role"] == "tool" and "委派深度上限" in m["content"] for m in kw["messages"]
        ), "超深度的派任务要作为工具结果回给模型"
        yield {"type": "done", "content": "我自己完成", "tool_calls": []}

    monkeypatch.setattr(orchestrator, "stream_chat", fake_stream_chat)
    run_id = client.post(f"/api/chats/{chat_id}/messages", json={"content": "调研并写稿"}).json()["run"]["id"]
    run_worker(run_id)
    wait_status(client, run_id, {"completed"})

    with SessionLocal() as db:
        tasks = db.scalars(select(Task).where(Task.run_id == run_id)).all()
    assert len(tasks) == 1, "深度用尽后不能再建子任务"


def test_nested_delegation_cannot_target_supervisor(client, invite, monkeypatch):
    """子 Bot 不能把任务派回总指挥，也不能派回自己的上级。"""
    from app.agent import orchestrator

    register(client, invite, "nested-target-guard@test.dev")
    chat_id, bots = setup_workspace(client)
    researcher = _pick(bots, "研究员")
    supervisor = _pick(bots, "总指挥")
    turns = {"supervisor": 0, "researcher": 0}

    async def fake_stream_chat(**kw):
        if kw["ctx"]["role"] == "supervisor":
            turns["supervisor"] += 1
            if turns["supervisor"] == 1:
                yield {
                    "type": "done",
                    "content": "",
                    "tool_calls": [_delegate_call("top-delegate", researcher["name"], "调研要点")],
                }
                return
            yield {"type": "done", "content": "完成", "tool_calls": []}
            return

        turns["researcher"] += 1
        if turns["researcher"] == 1:
            yield {
                "type": "done",
                "content": "",
                "tool_calls": [_delegate_call("bad-delegate", supervisor["name"], "帮我决定")],
            }
            return
        assert any(
            m["role"] == "tool" and "不能把任务派回总指挥" in m["content"] for m in kw["messages"]
        )
        yield {"type": "done", "content": "我自己判断", "tool_calls": []}

    monkeypatch.setattr(orchestrator, "stream_chat", fake_stream_chat)
    run_id = client.post(f"/api/chats/{chat_id}/messages", json={"content": "调研并判断"}).json()["run"]["id"]
    run_worker(run_id)
    wait_status(client, run_id, {"completed"})

    with SessionLocal() as db:
        tasks = db.scalars(select(Task).where(Task.run_id == run_id)).all()
    assert len(tasks) == 1


def test_nested_failure_returns_to_parent_worker(client, invite, monkeypatch):
    """写手失败只交回研究员，由研究员继续收尾，整个执行不算失败。"""
    from app.agent import orchestrator
    from app.agent.llm import LLMError

    register(client, invite, "nested-failure@test.dev")
    chat_id, bots = setup_workspace(client)
    researcher = _pick(bots, "研究员")
    writer = _pick(bots, "写手")
    turns = {"supervisor": 0, "researcher": 0}

    async def fake_stream_chat(**kw):
        role = kw["ctx"]["role"]
        bot_name = kw["ctx"]["bot_name"]
        if role == "supervisor":
            turns["supervisor"] += 1
            if turns["supervisor"] == 1:
                yield {
                    "type": "done",
                    "content": "",
                    "tool_calls": [_delegate_call("top-delegate", researcher["name"], "调研并成稿")],
                }
                return
            assert any(
                m["role"] == "tool" and "研究员兜底" in m["content"] for m in kw["messages"]
            )
            yield {"type": "done", "content": "已完成", "tool_calls": []}
            return

        if bot_name == "研究员":
            turns["researcher"] += 1
            if turns["researcher"] == 1:
                yield {
                    "type": "done",
                    "content": "",
                    "tool_calls": [_delegate_call("nested-delegate", writer["name"], "写稿")],
                }
                return
            assert any(
                m["role"] == "tool" and "失败" in m["content"] for m in kw["messages"]
            ), "写手失败的原因要交回研究员"
            yield {"type": "done", "content": "研究员兜底完成", "tool_calls": []}
            return

        raise LLMError("模型接口返回 500")

    monkeypatch.setattr(orchestrator, "stream_chat", fake_stream_chat)
    run_id = client.post(f"/api/chats/{chat_id}/messages", json={"content": "调研并成稿"}).json()["run"]["id"]
    run_worker(run_id)
    wait_status(client, run_id, {"completed"})

    with SessionLocal() as db:
        tasks = db.scalars(
            select(Task).where(Task.run_id == run_id).order_by(Task.created_at)
        ).all()
        assert len(tasks) == 2
        outer, inner = tasks
        assert outer.status == "completed"
        assert inner.status == "failed"


def test_delegation_depth_setting(client, invite):
    """RUN-09 / LIM-01：用户默认值可改，并做范围校验。"""
    register(client, invite, "depth-setting@test.dev")
    settings = client.get("/api/settings").json()
    assert settings["delegation_depth"] == 2

    assert client.patch("/api/settings", json={"delegation_depth": 3}).json()["delegation_depth"] == 3
    assert client.patch("/api/settings", json={"delegation_depth": 9}).status_code == 400
    assert client.patch("/api/settings", json={"delegation_depth": 0}).status_code == 400
