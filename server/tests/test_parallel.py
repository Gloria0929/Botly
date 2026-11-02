"""RUN-12：计划同批步骤并行执行。

覆盖：
1. 同一 batch 的两个步骤并行跑（模型只发了一个 delegate 也能补齐另一个）；
2. 两个子任务的结果都能回到总指挥（一个走 tool_result，一个走批次汇总）；
3. 用户停止父执行时，子执行与子任务一起停；
4. 父执行恢复是幂等的：重复唤醒不会重复建任务/重复汇总。
"""
from __future__ import annotations

import asyncio
import json

import pytest
from sqlalchemy import select

from app.core.db import SessionLocal
from app.core.models import Message, Run, Task
from app.worker import _wake_waiting_parents, process_one
from tests.conftest import login, register, setup_workspace


def run_worker(run_id: str) -> None:
    asyncio.run(process_one(run_id))


def wait_status(client, run_id: str, expect: set[str]) -> dict:
    run = client.get(f"/api/runs/{run_id}").json()
    assert run["status"] in expect, f"期望 {expect}，实际 {run['status']}（{run.get('end_reason')}）"
    return run


def drain_run(client, run_id: str, rounds: int = 12) -> dict:
    """模拟 Worker 循环：跑父执行 → 跑排队的子执行 → 唤醒父执行，直到结束。"""
    for _ in range(rounds):
        run_worker(run_id)
        run = client.get(f"/api/runs/{run_id}").json()
        if run["status"] in ("completed", "failed", "stopped", "waiting"):
            return run
        with SessionLocal() as db:
            child_ids = list(
                db.scalars(
                    select(Run.id).where(
                        Run.parent_run_id == run_id, Run.status == "queued"
                    )
                )
            )
        for child_id in child_ids:
            run_worker(child_id)
        _wake_waiting_parents()
    return client.get(f"/api/runs/{run_id}").json()


def messages(client, chat_id: str) -> list[dict]:
    return client.get(f"/api/chats/{chat_id}/messages?limit=200").json()["messages"]


def plan_steps(parallel: bool) -> list[dict]:
    steps = [
        {"title": "资料收集", "bot": "研究员", "expected": "要点清单", "batch": 1},
        {"title": "撰写成稿", "bot": "写手", "expected": "成稿", "batch": 1},
    ]
    if not parallel:
        for step in steps:
            step.pop("batch", None)
    return steps


def make_stream(parallel: bool, worker_results: dict[str, str], calls: dict):
    """总指挥第一轮出计划，第二轮只派第一步；子 Bot 各自返回结果。"""

    async def fake_stream_chat(**kw):
        ctx = kw.get("ctx") or {}
        if ctx.get("role") == "worker":
            name = ctx.get("bot_name") or ""
            calls["workers"].append(name)
            content = worker_results.get(name, f"{name} 完成")
            yield {"type": "done", "content": content, "tool_calls": []}
            return
        calls["supervisor"] += 1
        if calls["supervisor"] == 1:
            yield {
                "type": "done",
                "content": "",
                "tool_calls": [{
                    "id": "plan-1",
                    "type": "function",
                    "function": {
                        "name": "propose_plan",
                        "arguments": json.dumps({"steps": plan_steps(parallel)}, ensure_ascii=False),
                    },
                }],
            }
            return
        if calls["supervisor"] == 2:
            calls_to_send = [{
                "id": "delegate-1",
                "type": "function",
                "function": {
                    "name": "delegate",
                    "arguments": json.dumps({
                        "bot": "研究员",
                        "instruction": "收集三家厂商的价格与接入方式",
                        "step": "资料收集",
                        "expected_output": "要点清单",
                    }, ensure_ascii=False),
                },
            }]
            if not parallel:
                # 串行时模型按顺序把两步都发出来，由同一张图逐个执行
                calls_to_send.append({
                    "id": "delegate-2",
                    "type": "function",
                    "function": {
                        "name": "delegate",
                        "arguments": json.dumps({
                            "bot": "写手",
                            "instruction": "根据资料写一份成稿",
                            "step": "撰写成稿",
                            "expected_output": "成稿",
                        }, ensure_ascii=False),
                    },
                })
            yield {"type": "done", "content": "", "tool_calls": calls_to_send}
            return
        # 汇总轮：两个子任务的结果都必须能读到
        blob = json.dumps(kw["messages"], ensure_ascii=False)
        assert "研究员的结果" in blob
        assert "写手的结果" in blob
        calls["summary_input"] = blob
        yield {"type": "done", "content": "两份结果已汇总", "tool_calls": []}

    return fake_stream_chat


@pytest.mark.parametrize("parallel", [True, False])
def test_same_batch_runs_in_parallel(client, invite, monkeypatch, parallel):
    from app.agent import orchestrator

    register(client, invite, f"parallel-{parallel}@test.dev")
    chat_id, _ = setup_workspace(client)
    calls = {"supervisor": 0, "workers": []}
    monkeypatch.setattr(
        orchestrator,
        "stream_chat",
        make_stream(parallel, {"研究员": "研究员的结果", "写手": "写手的结果"}, calls),
    )

    run_id = client.post(
        f"/api/chats/{chat_id}/messages",
        json={"content": "收集资料并写一份成稿"},
    ).json()["run"]["id"]

    run_worker(run_id)
    wait_status(client, run_id, {"waiting"})  # 计划确认
    client.post(f"/api/runs/{run_id}/plan/approve", json={})

    run = drain_run(client, run_id)
    assert run["status"] == "completed", run

    tasks = client.get(f"/api/chats/{chat_id}").json()["tasks"]
    assert len(tasks) == 2, tasks
    assert all(t["status"] == "completed" for t in tasks)
    assert {t["batch"] for t in tasks} == ({1} if parallel else {0})

    with SessionLocal() as db:
        children = list(
            db.scalars(select(Run).where(Run.parent_run_id == run_id))
        )
        summary = list(
            db.scalars(
                select(Message).where(
                    Message.run_id == run_id,
                    Message.type == "system",
                    Message.content.like("并行批次 %"),
                )
            )
        )
    if parallel:
        # 同一批的两个步骤都各有一条独立执行
        assert len(children) == 2 and all(c.status == "completed" for c in children)
        assert len(summary) == 1, "批次结束后应有一条汇总，且只写一次"
        # 写手没有显式 delegate 调用，结果只能靠批次汇总进入总指挥上下文
        assert "写手的结果" in summary[0].content
    else:
        # 没写 batch 时保持串行：不产生子执行，也不写并行汇总
        assert children == []
        assert summary == []

    assert any(m["content"] == "两份结果已汇总" for m in messages(client, chat_id))

    # 恢复幂等：再唤醒一次、再跑一次，不应该多出任务或汇总
    with SessionLocal() as db:
        tasks_before = len(db.scalars(select(Task).where(Task.run_id == run_id)).all())
    _wake_waiting_parents()
    run_worker(run_id)
    with SessionLocal() as db:
        tasks_after = len(db.scalars(select(Task).where(Task.run_id == run_id)).all())
        summary_after = list(
            db.scalars(
                select(Message).where(
                    Message.run_id == run_id,
                    Message.type == "system",
                    Message.content.like("并行批次 %"),
                )
            )
        )
    assert tasks_after == tasks_before
    assert len(summary_after) == len(summary)


def test_stop_parent_stops_children(client, invite, monkeypatch):
    from app.agent import orchestrator

    register(client, invite, "parallel-stop@test.dev")
    chat_id, _ = setup_workspace(client)
    calls = {"supervisor": 0, "workers": []}
    monkeypatch.setattr(
        orchestrator,
        "stream_chat",
        make_stream(True, {"研究员": "研究员的结果", "写手": "写手的结果"}, calls),
    )

    run_id = client.post(
        f"/api/chats/{chat_id}/messages", json={"content": "并行跑两份活"}
    ).json()["run"]["id"]
    run_worker(run_id)
    wait_status(client, run_id, {"waiting"})
    client.post(f"/api/runs/{run_id}/plan/approve", json={})

    # 父执行进入等待并行子任务
    run_worker(run_id)
    run = wait_status(client, run_id, {"waiting_children"})
    assert run["status"] == "waiting_children"

    with SessionLocal() as db:
        children = list(db.scalars(select(Run).where(Run.parent_run_id == run_id)))
    assert len(children) == 2 and all(c.status == "queued" for c in children)

    assert client.post(f"/api/runs/{run_id}/stop").status_code == 200

    with SessionLocal() as db:
        children = list(db.scalars(select(Run).where(Run.parent_run_id == run_id)))
        tasks = list(db.scalars(select(Task).where(Task.run_id == run_id)))
    assert all(c.status == "stopped" for c in children), [c.status for c in children]
    assert all(t.status == "canceled" for t in tasks), [t.status for t in tasks]

    # 子执行不会再被 Worker 认领执行
    for child in children:
        run_worker(child.id)
    with SessionLocal() as db:
        still = list(db.scalars(select(Run).where(Run.parent_run_id == run_id)))
    assert all(c.status == "stopped" for c in still)
    assert not [m for m in messages(client, chat_id) if m["content"] == "写手的结果"]


def test_child_executions_really_run_concurrently(client, invite, monkeypatch):
    """两条子执行在同一个 Worker 进程里同时跑，而不是排队串行。"""
    import time

    from app.agent import orchestrator

    register(client, invite, "parallel-concurrent@test.dev")
    chat_id, _ = setup_workspace(client)
    calls = {"supervisor": 0, "workers": []}

    async def slow_worker_stream(**kw):
        ctx = kw.get("ctx") or {}
        if ctx.get("role") == "worker":
            await asyncio.sleep(0.3)
            yield {"type": "done", "content": f"{ctx.get('bot_name')} 完成", "tool_calls": []}
            return
        calls["supervisor"] += 1
        if calls["supervisor"] == 1:
            yield {"type": "done", "content": "", "tool_calls": [{
                "id": "plan-c", "type": "function", "function": {
                    "name": "propose_plan",
                    "arguments": json.dumps({"steps": plan_steps(True)}, ensure_ascii=False),
                },
            }]}
            return
        if calls["supervisor"] == 2:
            yield {"type": "done", "content": "", "tool_calls": [{
                "id": "delegate-c", "type": "function", "function": {
                    "name": "delegate",
                    "arguments": json.dumps({
                        "bot": "研究员", "instruction": "收集资料", "step": "资料收集",
                    }, ensure_ascii=False),
                },
            }]}
            return
        yield {"type": "done", "content": "都完成了", "tool_calls": []}

    monkeypatch.setattr(orchestrator, "stream_chat", slow_worker_stream)
    run_id = client.post(
        f"/api/chats/{chat_id}/messages", json={"content": "并行跑两份活"}
    ).json()["run"]["id"]
    run_worker(run_id)
    wait_status(client, run_id, {"waiting"})
    client.post(f"/api/runs/{run_id}/plan/approve", json={})
    run_worker(run_id)
    wait_status(client, run_id, {"waiting_children"})

    with SessionLocal() as db:
        child_ids = list(db.scalars(select(Run.id).where(Run.parent_run_id == run_id)))
    assert len(child_ids) == 2

    async def run_both():
        await asyncio.gather(*(process_one(cid) for cid in child_ids))

    started = time.time()
    asyncio.run(run_both())
    elapsed = time.time() - started
    assert elapsed < 0.55, f"两条子执行应并行（0.3s 的模型调用），实际 {elapsed:.2f}s"
    with SessionLocal() as db:
        children = list(db.scalars(select(Run).where(Run.parent_run_id == run_id)))
    assert all(c.status == "completed" for c in children)
