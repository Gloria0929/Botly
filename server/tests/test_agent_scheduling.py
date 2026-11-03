"""Agent 自主决定依赖，计划确认后按依赖并行/串行执行，无需用户指示。"""
from __future__ import annotations

import asyncio
import json

import pytest
from sqlalchemy import select

from app.agent import orchestrator
from app.agent.orchestrator import RunExecutor
from app.agent.scheduling import schedule_plan
from app.tools.registry import PLAN_TOOL, schema_list
from app.core.db import SessionLocal
from app.core.models import Bot, Message, Provider, Run, Task
from tests.conftest import register, setup_workspace
from tests.test_parallel import drain_run, run_worker
from tests.test_run_reliability import _new_run


def step(title, depends_on=None, bot="研究员"):
    return {"title": title, "bot": bot, "depends_on": depends_on or []}


def test_dependency_schedule_reorders_by_readiness_and_limits_capacity():
    plan = [step("写报告", ["查竞品", "算数据"], "架构师"), step("查竞品"), step("算数据")]
    scheduled = schedule_plan(plan)
    assert [(s["title"], s["batch"]) for s in scheduled] == [("查竞品", 1), ("算数据", 1), ("写报告", 2)]
    many = [step(f"调查{i}") for i in range(6)] + [step("汇总", [f"调查{i}" for i in range(6)])]
    assert [s["batch"] for s in schedule_plan(many)] == [1, 1, 1, 1, 2, 2, 3]
    assert all("batch" not in s for s in plan), "不得修改模型原始输入"


@pytest.mark.parametrize("plan", [
    [step("A", ["B"]), step("B", ["A"])],
    [step("A", ["missing"])], [step("A", ["A"])],
    [step("A"), step("A")], [step("A"), {"title": "B", "bot": "研究员"}],
])
def test_invalid_dependencies_are_rejected(plan):
    with pytest.raises(ValueError):
        schedule_plan(plan)


def test_legacy_large_parallel_batch_is_split_instead_of_serialized():
    plan = [{"title": f"调查{i}", "bot": "研究员", "batch": 1} for i in range(6)]
    assert [s["batch"] for s in schedule_plan(plan)] == [1, 1, 1, 1, 2, 2]


def test_model_tool_schema_requires_explicit_dependency_decision():
    schema = schema_list([PLAN_TOOL])[0]["function"]["parameters"]
    assert "depends_on" in schema["properties"]["steps"]["items"]["required"]
    assert "depends_on" not in PLAN_TOOL.parameters["properties"]["steps"]["items"]["required"], "不能修改历史格式校验规则"


@pytest.mark.parametrize("profile", ["independent", "dependent", "mixed", "capacity"])
def test_agent_decides_schedule_without_parallel_request(client, invite, monkeypatch, profile):
    register(client, invite, f"auto-schedule-{profile}@test.dev")
    chat_id, _ = setup_workspace(client)
    if profile == "independent":
        plan = [step("查竞品"), step("分析已有资料", bot="架构师")]
        expected_batches = [1, 1]
    elif profile == "dependent":
        plan = [step("查竞品"), step("写报告", ["查竞品"], "架构师")]
        expected_batches = [0, 0]
    elif profile == "mixed":
        plan = [step("查竞品"), step("分析已有资料", bot="架构师"), step("最终汇总", ["查竞品", "分析已有资料"])]
        expected_batches = [1, 1, 0]
    else:
        plan = [step(f"调查{i}") for i in range(6)] + [step("汇总", [f"调查{i}" for i in range(6)], "架构师")]
        expected_batches = [1, 1, 1, 1, 2, 2, 0]
    turns = {"n": 0}
    run_id = None

    async def fake_stream_chat(**kw):
        if kw["ctx"]["role"] == "worker":
            with SessionLocal() as db:
                task = db.scalar(select(Task).where(Task.instruction == kw["ctx"]["task"], Task.run_id == run_id))
                if task:
                    spec = next(s for s in plan if s["title"] == task.step_key)
                    finished = {t.step_key for t in db.scalars(select(Task).where(Task.run_id == run_id, Task.status == "completed"))}
                    assert set(spec["depends_on"]) <= finished, "依赖未完成时不能启动任务"
            yield {"type": "done", "content": kw["ctx"]["task"] + " 已完成", "tool_calls": []}
            return
        turns["n"] += 1
        if turns["n"] == 1:
            current = next(m["content"] for m in kw["messages"] if m["role"] == "user")
            assert "并行" not in current and "串行" not in current
            assert "自主决定并行或串行" in kw["messages"][0]["content"]
            yield {"type": "done", "content": "", "tool_calls": [{"id": "auto-plan", "type": "function",
                "function": {"name": "propose_plan", "arguments": json.dumps({"steps": plan}, ensure_ascii=False)}}]}
            return
        with SessionLocal() as db:
            pending = [s for s in schedule_plan(plan) if not db.scalar(select(Task.id).where(
                Task.run_id == run_id, Task.step_key == s["title"], Task.status == "completed"))]
        if not pending:
            yield {"type": "done", "content": "完成所有任务。", "tool_calls": []}
            return
        target = pending[0]
        # 模型每次只派一项，执行器也必须自动补齐同批独立项。
        yield {"type": "done", "content": "", "tool_calls": [{"id": f"dispatch-{turns['n']}", "type": "function",
            "function": {"name": "delegate", "arguments": json.dumps({
                "bot": target["bot"], "instruction": target["title"], "step": target["title"],
            }, ensure_ascii=False)}}]}

    monkeypatch.setattr(orchestrator, "stream_chat", fake_stream_chat)
    run_id = client.post(f"/api/chats/{chat_id}/messages", json={"content": "请完成这份项目研究报告。"}).json()["run"]["id"]
    run_worker(run_id)
    proposed = client.get(f"/api/runs/{run_id}").json()
    assert proposed["status"] == "waiting"
    assert [s["batch"] for s in proposed["resume"]["plan"]] == [s["batch"] for s in schedule_plan(plan)]
    # 用户只确认目标与计划，不提供 batch 或并行指令。
    assert client.post(f"/api/runs/{run_id}/plan/approve", json={}).status_code == 200
    result = drain_run(client, run_id, rounds=20)
    assert result["status"] == "completed", result
    with SessionLocal() as db:
        tasks = db.scalars(select(Task).where(Task.run_id == run_id).order_by(Task.created_at)).all()
        assert [t.batch for t in tasks] == expected_batches
        assert all(t.status == "completed" for t in tasks)


def test_dependent_step_cannot_be_dispatched_before_inputs(client, invite):
    ctx = _new_run(client, invite, "dependency-guard@test.dev")
    plan = [step("查竞品"), step("写报告", ["查竞品"], "架构师")]
    with SessionLocal() as db:
        orchestrator.add_message(db, thread=db.get(orchestrator.Thread, ctx["main_id"]), run_id=ctx["run_id"],
                                 sender_type="bot", type_="plan", meta={"status": "approved", "steps": schedule_plan(plan)})
        db.commit()
    call = {"id": "too-early", "type": "function", "function": {"name": "delegate", "arguments": json.dumps({
        "bot": "架构师", "instruction": "写报告", "step": "写报告"}, ensure_ascii=False)}}
    result = asyncio.run(RunExecutor(ctx["run_id"]).start_delegate(
        thread_id=ctx["main_id"], bot_id=ctx["supervisor_id"], task_id=None, call=call))
    assert result == {}
    with SessionLocal() as db:
        assert not db.scalar(select(Task.id).where(Task.run_id == ctx["run_id"]))
        assert db.scalar(select(Message).where(Message.run_id == ctx["run_id"], Message.type == "tool_result")).content.startswith("前置批次尚未完成")


def test_real_agent_must_decide_dependencies_before_showing_plan(client, invite):
    ctx = _new_run(client, invite, "missing-dependencies@test.dev")
    with SessionLocal() as db:
        provider = db.get(Provider, db.get(Bot, ctx["supervisor_id"]).provider_id)
        provider.kind = "openai"
        run = db.get(Run, ctx["run_id"])
        run.config_snapshot = {}
        db.commit()
    call = {"id": "incomplete-plan", "type": "function", "function": {"name": "propose_plan", "arguments": json.dumps({
        "steps": [{"title": "调研", "bot": "研究员"}, {"title": "写稿", "bot": "架构师"}]}, ensure_ascii=False)}}
    result = asyncio.run(RunExecutor(ctx["run_id"])._handle_tool_call(
        thread_id=ctx["main_id"], bot_id=ctx["supervisor_id"], task_id=None, call=call, is_supervisor=True))
    assert "自主判断" in result and "不要询问用户" in result
    with SessionLocal() as db:
        assert not db.scalar(select(Message.id).where(Message.run_id == ctx["run_id"], Message.type == "plan"))


def test_edited_plan_recalculates_batches_and_rejects_cycles(client, invite):
    ctx = _new_run(client, invite, "edited-dependencies@test.dev")
    with SessionLocal() as db:
        run = db.get(Run, ctx["run_id"])
        run.status = "waiting"
        run.resume_payload = {"kind": "plan", "plan": [step("A"), step("B", ["A"])]}
        db.commit()
    bad = client.post(f"/api/runs/{ctx['run_id']}/plan/approve", json={"steps": [step("A", ["B"]), step("B", ["A"])]})
    assert bad.status_code == 400
    assert client.get(f"/api/runs/{ctx['run_id']}").json()["status"] == "waiting"
    good = client.post(f"/api/runs/{ctx['run_id']}/plan/approve", json={"steps": [step("A"), step("B")]})
    assert good.status_code == 200
    assert [s["batch"] for s in good.json()["resume"]["plan"]] == [1, 1]


def test_updated_dependency_plan_replaces_old_schedule(client, invite):
    ctx = _new_run(client, invite, "adjusted-dependencies@test.dev")
    with SessionLocal() as db:
        thread = db.get(orchestrator.Thread, ctx["main_id"])
        orchestrator.add_message(db, thread=thread, run_id=ctx["run_id"], sender_type="bot", type_="plan",
                                 meta={"status": "approved", "steps": [step("A"), step("B", ["A"])]})
        orchestrator.add_message(db, thread=thread, run_id=ctx["run_id"], sender_type="system", type_="system",
                                 meta={"kind": "plan_adjusted", "steps": [step("A"), step("B")]})
        db.flush()
        assert [s["batch"] for s in RunExecutor._approved_plan_steps(db, ctx["run_id"])] == [1, 1]
