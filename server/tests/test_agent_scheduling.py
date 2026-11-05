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
from app.core.models import Bot, Chat, Message, Provider, Run, Task, Thread
from app.agent.signals import AwaitChildren
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
        assert db.scalar(select(Message).where(Message.run_id == ctx["run_id"], Message.type == "tool_result")).content.startswith("前置依赖尚未完成")


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


def approve_steps(ctx, plan):
    with SessionLocal() as db:
        orchestrator.add_message(db, thread=db.get(Thread, ctx["main_id"]), run_id=ctx["run_id"],
                                 sender_type="bot", type_="plan", meta={"status": "approved", "steps": plan})
        db.commit()


def record_task(ctx, title, status, *, parent=None, result="", attempt=1):
    with SessionLocal() as db:
        thread = Thread(user_id=ctx["user_id"], chat_id=ctx["chat_id"], type="task",
                        parent_thread_id=parent or ctx["main_id"])
        db.add(thread)
        db.flush()
        task = Task(user_id=ctx["user_id"], run_id=ctx["run_id"], thread_id=thread.id,
                    assigned_bot_id=ctx["bots"]["研究员"]["id"], step_key=orchestrator._step_key(title),
                    instruction=title, status=status, result=result, attempt=attempt)
        db.add(task)
        db.commit()
        return task.id, thread.id


def dispatch(ctx, title, *, bot="研究员", instruction=None, call_id="dispatch", execute=True):
    call = {"id": call_id, "type": "function", "function": {"name": "delegate", "arguments": json.dumps({
        "bot": bot, "step": title, "instruction": instruction or title,
    }, ensure_ascii=False)}}
    with SessionLocal() as db:
        orchestrator.add_message(db, thread=db.get(Thread, ctx["main_id"]), run_id=ctx["run_id"],
                                 sender_type="bot", sender_bot_id=ctx["supervisor_id"], type_="tool_call",
                                 meta={"tool_calls": [call]})
        db.commit()
    if execute:
        return asyncio.run(RunExecutor(ctx["run_id"]).start_delegate(
            thread_id=ctx["main_id"], bot_id=ctx["supervisor_id"], task_id=None, call=call))


def test_capacity_rechecks_ready_steps_after_each_batch():
    plan = [step(f"调查{i}") for i in range(5)] + [step(f"成稿{i}", ["调查0"]) for i in range(3)]
    scheduled = schedule_plan(plan)
    assert [s["batch"] for s in scheduled] == [1, 1, 1, 1, 2, 2, 2, 2]


@pytest.mark.parametrize("plan", [
    [step("Research"), step("research")], [step("资料  收集"), step("资料 收集")],
    [step("A" * 200 + "1"), step("A" * 200 + "2")],
    [{"title": 123, "depends_on": []}], [None],
])
def test_invalid_or_colliding_step_keys_are_rejected(plan):
    with pytest.raises(ValueError):
        schedule_plan(plan)


def test_serial_step_number_uses_canonical_identity(client, invite):
    ctx = _new_run(client, invite, "canonical-number@test.dev")
    approve_steps(ctx, [step("查竞品"), step("写报告", ["查竞品"])])
    first = dispatch(ctx, "1", instruction="查竞品")
    with SessionLocal() as db:
        task = db.get(Task, first["task_id"])
        assert task.step_key == "查竞品"
        task.status = "completed"
        db.commit()
    assert dispatch(ctx, "写报告", call_id="report")["role"] == "worker"


@pytest.mark.parametrize("title", ["不存在的步骤", "调研报告"])
def test_unmatched_or_ambiguous_step_cannot_bypass_dependencies(client, invite, title):
    ctx = _new_run(client, invite, f"bad-step-{title}@test.dev")
    approve_steps(ctx, [step("资料"), step("调研报告：国内", ["资料"]), step("调研报告：海外", ["资料"])])
    assert dispatch(ctx, title) == {}
    with SessionLocal() as db:
        assert not db.scalar(select(Task.id).where(Task.run_id == ctx["run_id"]))


def test_unrelated_failure_does_not_block_ready_task(client, invite):
    ctx = _new_run(client, invite, "unrelated-failure@test.dev")
    approve_steps(ctx, [step("查竞品"), step("无关调查"), step("写报告", ["查竞品"])])
    record_task(ctx, "查竞品", "completed")
    record_task(ctx, "无关调查", "failed")
    assert dispatch(ctx, "写报告")["role"] == "worker"


@pytest.mark.parametrize("stale", ["revision", "nested"])
def test_only_latest_direct_task_can_satisfy_dependency(client, invite, stale):
    ctx = _new_run(client, invite, f"stale-dependency-{stale}@test.dev")
    approve_steps(ctx, [step("查竞品"), step("写报告", ["查竞品"])])
    if stale == "revision":
        record_task(ctx, "查竞品", "completed")
        record_task(ctx, "查竞品", "failed", attempt=2)
    else:
        _, parent = record_task(ctx, "其他工作", "running")
        record_task(ctx, "查竞品", "completed", parent=parent)
    assert dispatch(ctx, "写报告") == {}


def test_parallel_numbered_calls_preserve_each_assignment(client, invite):
    ctx = _new_run(client, invite, "parallel-assignment@test.dev")
    approve_steps(ctx, [step("查竞品"), step("算数据")])
    dispatch(ctx, "2", bot="架构师", instruction="只核对收入数据", call_id="second", execute=False)
    with pytest.raises(AwaitChildren):
        dispatch(ctx, "1", bot="架构师", instruction="只调查定价", call_id="first")
    with SessionLocal() as db:
        tasks = db.scalars(select(Task).where(Task.run_id == ctx["run_id"]).order_by(Task.created_at)).all()
        assert [(t.step_key, t.instruction) for t in tasks] == [("查竞品", "只调查定价"), ("算数据", "只核对收入数据")]
        assert all(t.assigned_bot_id == ctx["bots"]["架构师"]["id"] for t in tasks)
        notes = db.scalars(select(Message).where(Message.run_id == ctx["run_id"], Message.type == "text")).all()
        assert {m.meta["tool_call_id"] for m in notes if m.meta.get("delegation")} == {"first", "second"}


def test_parallel_batch_rejects_disabled_sibling_without_partial_dispatch(client, invite):
    ctx = _new_run(client, invite, "disabled-batch@test.dev")
    approve_steps(ctx, [step("查竞品"), step("算数据", bot="架构师")])
    with SessionLocal() as db:
        db.get(Bot, ctx["bots"]["架构师"]["id"]).enabled = False
        db.commit()
    assert dispatch(ctx, "查竞品") == {}
    with SessionLocal() as db:
        assert not db.scalar(select(Task.id).where(Task.run_id == ctx["run_id"]))
        result = db.scalar(select(Message).where(Message.run_id == ctx["run_id"], Message.type == "tool_result"))
        assert "算数据" in result.content and "停用" in result.content


def test_starting_batch_does_not_retry_failed_siblings(client, invite):
    ctx = _new_run(client, invite, "no-implicit-retry@test.dev")
    approve_steps(ctx, [step("查竞品"), step("算数据")])
    record_task(ctx, "查竞品", "failed")
    with pytest.raises(AwaitChildren):
        dispatch(ctx, "算数据")
    with SessionLocal() as db:
        assert len(db.scalars(select(Task).where(Task.run_id == ctx["run_id"], Task.step_key == "查竞品")).all()) == 1


def test_parallel_sibling_checks_its_own_dependencies(client, invite):
    ctx = _new_run(client, invite, "sibling-dependency@test.dev")
    approve_steps(ctx, [step("查竞品"), step("算数据"), step("报告", ["查竞品"]), step("图表", ["算数据"])])
    record_task(ctx, "查竞品", "completed")
    record_task(ctx, "算数据", "failed")
    with pytest.raises(AwaitChildren):
        dispatch(ctx, "报告")
    with SessionLocal() as db:
        assert not db.scalar(select(Task.id).where(Task.run_id == ctx["run_id"], Task.step_key == "图表"))
        assert db.scalar(select(Task.id).where(Task.run_id == ctx["run_id"], Task.step_key == "报告"))


def test_explicit_retry_only_reassigns_requested_step(client, invite):
    ctx = _new_run(client, invite, "explicit-retry@test.dev")
    approve_steps(ctx, [step("查竞品"), step("算数据")])
    record_task(ctx, "查竞品", "failed")
    record_task(ctx, "算数据", "failed")
    retry = dispatch(ctx, "1", bot="架构师", instruction="复用缓存重新核对价格")
    with SessionLocal() as db:
        task = db.get(Task, retry["task_id"])
        assert task.step_key == "查竞品" and task.attempt == 2
        assert task.assigned_bot_id == ctx["bots"]["架构师"]["id"]
        assert len(db.scalars(select(Task).where(Task.run_id == ctx["run_id"])).all()) == 3


def test_step_mode_still_dispatches_only_one_task(client, invite):
    ctx = _new_run(client, invite, "step-mode-schedule@test.dev")
    approve_steps(ctx, [step("查竞品"), step("算数据")])
    with SessionLocal() as db:
        db.get(Chat, ctx["chat_id"]).run_mode = "step"
        db.commit()
    assert dispatch(ctx, "2")["role"] == "worker"
    with SessionLocal() as db:
        tasks = db.scalars(select(Task).where(Task.run_id == ctx["run_id"])).all()
        assert len(tasks) == 1 and tasks[0].step_key == "算数据" and tasks[0].batch == 0


@pytest.mark.parametrize("parallel", [False, True])
def test_dependent_workers_receive_latest_input_results(client, invite, parallel):
    ctx = _new_run(client, invite, f"dependency-inputs-{parallel}@test.dev")
    plan = [step("调研"), step("报告", ["调研"])]
    if parallel:
        plan.append(step("图表", ["调研"]))
    approve_steps(ctx, plan)
    record_task(ctx, "调研", "completed", result="旧版本数据已经失效")
    record_task(ctx, "调研", "completed", result="最新样本共 42 条，详见 tasks/research/data.csv")
    if parallel:
        with pytest.raises(AwaitChildren):
            dispatch(ctx, "报告")
    else:
        dispatch(ctx, "报告")
    ex = RunExecutor(ctx["run_id"])
    with SessionLocal() as db:
        chat = db.get(Chat, ctx["chat_id"])
        workers = db.scalars(select(Task).where(Task.run_id == ctx["run_id"], Task.step_key != "调研")).all()
        assert len(workers) == (2 if parallel else 1)
        for task in workers:
            run = db.scalar(select(Run).where(Run.parent_task_id == task.id)) or db.get(Run, ctx["run_id"])
            messages = ex._build_messages(db, run, chat, db.get(Thread, task.thread_id),
                                         db.get(Bot, task.assigned_bot_id), False, ex._member_bots(db, chat, run))
            content = json.dumps(messages, ensure_ascii=False)
            assert "最新样本共 42 条" in content and "tasks/research/data.csv" in content
            assert "旧版本数据已经失效" not in content


def test_parallel_start_does_not_consume_explicit_sibling_revision(client, invite):
    ctx = _new_run(client, invite, "sibling-revision@test.dev")
    approve_steps(ctx, [step("查竞品"), step("算数据")])
    record_task(ctx, "查竞品", "completed")
    dispatch(ctx, "查竞品", instruction="只更新价格数据", call_id="revision", execute=False)
    with pytest.raises(AwaitChildren):
        dispatch(ctx, "算数据")
    with SessionLocal() as db:
        assert not db.scalar(select(Message.id).where(Message.run_id == ctx["run_id"],
            Message.type == "tool_result", Message.meta["tool_call_id"].as_string() == "revision"))
