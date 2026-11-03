"""目标由总指挥澄清，执行方法由子 Agent 决定；咨询覆盖串行、并行和重放。"""
from __future__ import annotations

import asyncio
import json

import pytest
from sqlalchemy import func, select

from app.agent import orchestrator
from app.agent.orchestrator import RunExecutor
from app.core.db import SessionLocal
from app.core.models import Bot, Chat, Message, Question, Run, Task, UsageLog
from app.tools.registry import tools_for_bot
from tests.conftest import register, setup_workspace
from tests.test_parallel import drain_run, run_worker
from tests.test_subtask_mirror import _setup


def call(name, args, call_id):
    return {"id": call_id, "type": "function", "function": {
        "name": name, "arguments": json.dumps(args, ensure_ascii=False),
    }}


def test_consultation_tool_is_only_offered_to_task_workers():
    def names(supervisor=False, task=False):
        return {t.name for t in tools_for_bot([], supervisor, can_consult_supervisor=task)}
    assert "ask_supervisor" in names(task=True)
    assert "ask_supervisor" not in names()
    assert "ask_supervisor" not in names(supervisor=True, task=True)


@pytest.mark.parametrize("parallel", [False, True])
def test_worker_consults_then_chooses_own_method_and_continues(client, invite, monkeypatch, parallel):
    register(client, invite, f"consultation-{parallel}@test.dev")
    chat_id, bots = setup_workspace(client)
    supervisor = next(b for b in bots if b["role"] == "总指挥")
    worker = next(b for b in bots if b["role"] == "研究员")
    writer = next(b for b in bots if b["role"] == "架构师")
    client.patch(f"/api/chats/{chat_id}", json={"limits_override": {"delegation_depth": 1}})
    turns = {"supervisor": 0, "worker": 0, "consultation": 0}
    original = "面向产品经理整理一份中文简报，具体形式自行判断。"

    async def fake_stream_chat(**kw):
        if kw["ctx"].get("consultation"):
            turns["consultation"] += 1
            assert kw["tools"] is None, "澄清不应执行子任务或派新任务"
            context = json.loads(kw["messages"][-1]["content"])
            assert context["original_request"] == original
            assert context["worker_question"] == "这份简报面向谁？"
            assert context["task_goal"] == "整理简报"
            assert "具体执行方法、工具、技能和操作步骤仍由子 Agent 自主选择" in kw["messages"][0]["content"]
            yield {"type": "done", "content": "面向产品经理，验收重点是中文表达清楚、结论有依据。具体形式由你判断。",
                   "tool_calls": [], "usage": {"input_tokens": 17, "output_tokens": 3}}
            return
        if kw["ctx"]["role"] == "supervisor":
            turns["supervisor"] += 1
            assert "不替它写操作教程" in kw["messages"][0]["content"]
            if parallel and turns["supervisor"] == 1:
                yield {"type": "done", "content": "", "tool_calls": [call("propose_plan", {"steps": [
                    {"title": "整理简报", "bot": worker["name"], "depends_on": []},
                    {"title": "独立复核", "bot": writer["name"], "depends_on": []},
                ]}, "consult-plan")]}
            elif turns["supervisor"] == (2 if parallel else 1):
                yield {"type": "done", "content": "", "tool_calls": [call("delegate", {
                    "bot": worker["name"], "instruction": "整理简报", "step": "整理简报", "expected_output": "中文简报",
                }, "consult-delegate")]}
            else:
                yield {"type": "done", "content": "简报已验收并汇总。", "tool_calls": []}
            return
        if kw["ctx"]["bot_name"] != worker["name"]:
            yield {"type": "done", "content": "独立复核已完成。", "tool_calls": []}
            return
        turns["worker"] += 1
        tools = {t["function"]["name"] for t in kw["tools"]}
        assert "ask_supervisor" in tools, "即使已达委派深度上限也能向主 Agent 澄清"
        assert "delegate" not in tools
        assert "你自主分析任务、选择实现方法、步骤、工具和技能" in kw["messages"][0]["content"]
        if turns["worker"] == 1:
            yield {"type": "done", "content": "我先确认读者范围。", "tool_calls": [call("ask_supervisor", {
                "question": "这份简报面向谁？", "context": "已确定需要中文简报，读者影响内容深度。",
            }, "consult-audience")]}
        elif turns["worker"] == 2:
            assert any(m["role"] == "tool" and "面向产品经理" in m["content"] for m in kw["messages"])
            # 子 Agent 自己选择输出方法；总指挥没有给它指定工具或格式。
            yield {"type": "done", "content": "我决定用 Markdown 表格组织简报。", "tool_calls": [call("file_write", {
                "path": "brief.md", "content": "# 产品经理简报\n\n| 结论 | 依据 |\n|---|---|\n| 需核实 | 用户资料 |",
            }, "write-independent")]}
        else:
            yield {"type": "done", "content": "简报已写入 brief.md。", "tool_calls": []}

    monkeypatch.setattr(orchestrator, "stream_chat", fake_stream_chat)
    run_id = client.post(f"/api/chats/{chat_id}/messages", json={"content": original}).json()["run"]["id"]
    if parallel:
        run_worker(run_id)
        assert client.get(f"/api/runs/{run_id}").json()["status"] == "waiting"
        client.post(f"/api/runs/{run_id}/plan/approve", json={})
    result = drain_run(client, run_id)
    assert result["status"] == "completed", result
    assert turns["consultation"] == 1 and turns["worker"] == 3
    assert client.get(f"/api/chats/{chat_id}/files").json()[0]["path"] == "brief.md"
    with SessionLocal() as db:
        task = db.scalar(select(Task).where(Task.run_id == run_id, Task.assigned_bot_id == worker["id"]))
        logs = db.scalars(select(UsageLog).where(UsageLog.run_id == run_id, UsageLog.bot_id == supervisor["id"], UsageLog.output_tokens == 3)).all()
        assert len(logs) == 1 and logs[0].task_id is None, "澄清用量计入主 Agent"
        tool_msg = db.scalar(select(Message).where(Message.thread_id == task.thread_id, Message.type == "tool_call",
                                                  Message.meta["tool_calls"][0]["function"]["name"].as_string() == "ask_supervisor"))
        owning_run = tool_msg.run_id
        before = db.scalar(select(func.count(UsageLog.id)))
    reply = asyncio.run(RunExecutor(owning_run)._consult_supervisor(
        thread_id=task.thread_id, bot_id=worker["id"], task_id=task.id,
        call=tool_msg.meta["tool_calls"][0], args={"question": "这份简报面向谁？"},
    ))
    assert "面向产品经理" in reply and turns["consultation"] == 1
    with SessionLocal() as db:
        assert db.scalar(select(func.count(UsageLog.id))) == before
        assert not db.scalar(select(Question.id).where(Question.run_id == run_id)), "可由主 Agent 澄清时不打断用户"


def test_consultation_cannot_access_foreign_task_or_wrong_worker(client, invite, monkeypatch):
    ctx = _setup(client, invite, "consultation-scope@test.dev")

    async def no_model(**kw):
        raise AssertionError("未授权咨询不得调用模型")
        yield

    monkeypatch.setattr(orchestrator, "stream_chat", no_model)
    tool_call = call("ask_supervisor", {"question": "请澄清范围"}, "scope-question")
    for bot_id, thread_id in (("other-bot", ctx["task_thread_id"]), (ctx["worker_id"], ctx["main_id"])):
        reply = asyncio.run(RunExecutor(ctx["run_id"])._consult_supervisor(
            thread_id=thread_id, bot_id=bot_id, task_id=ctx["task_id"], call=tool_call, args={"question": "请澄清范围"},
        ))
        assert reply.startswith("无法澄清")
    reply = asyncio.run(RunExecutor(ctx["run_id"])._handle_tool_call(
        thread_id=ctx["main_id"], bot_id=ctx["worker_id"], task_id=None, call=tool_call, is_supervisor=False,
    ))
    assert "只有正在执行子任务" in reply


def test_supervisor_does_not_invent_missing_user_decisions(client, invite, monkeypatch):
    ctx = _setup(client, invite, "consultation-unknown@test.dev")
    with SessionLocal() as db:
        run = db.get(Run, ctx["run_id"])
        main_id = db.get(Chat, ctx["chat_id"]).supervisor_bot_id
        db.get(Bot, main_id).model = "specific-supervisor-model"
        run.config_snapshot = {}
        db.commit()

    async def clarify(**kw):
        assert kw["model"] == "specific-supervisor-model"
        assert "不要虚构用户偏好" in kw["messages"][0]["content"]
        yield {"type": "done", "content": "现有请求没有指定截止日期，需要向用户确认交付时间。", "tool_calls": []}

    monkeypatch.setattr(orchestrator, "stream_chat", clarify)
    reply = asyncio.run(RunExecutor(ctx["run_id"])._consult_supervisor(
        thread_id=ctx["task_thread_id"], bot_id=ctx["worker_id"], task_id=ctx["task_id"],
        call=call("ask_supervisor", {"question": "截止日期是什么？"}, "missing-date"), args={"question": "截止日期是什么？"},
    ))
    assert "需要向用户确认" in reply


def test_only_unresolved_user_decision_escalates_after_consultation(client, invite, monkeypatch):
    register(client, invite, "consultation-escalation@test.dev")
    chat_id, bots = setup_workspace(client)
    worker = next(b for b in bots if b["role"] == "研究员")
    turns = {"supervisor": 0, "worker": 0, "consultation": 0}

    async def fake_stream_chat(**kw):
        if kw["ctx"].get("consultation"):
            turns["consultation"] += 1
            yield {"type": "done", "content": "现有目标没有说明面向谁，请向用户确认目标读者。", "tool_calls": []}
        elif kw["ctx"]["role"] == "supervisor":
            turns["supervisor"] += 1
            if turns["supervisor"] == 1:
                yield {"type": "done", "content": "", "tool_calls": [call("delegate", {
                    "bot": worker["name"], "instruction": "整理简报", "expected_output": "简报",
                }, "escalation-delegate")]}
            else:
                yield {"type": "done", "content": "已验收简报。", "tool_calls": []}
        else:
            turns["worker"] += 1
            if turns["worker"] == 1:
                yield {"type": "done", "content": "", "tool_calls": [call("ask_supervisor", {
                    "question": "目标读者是谁？",
                }, "escalation-consult")]}
            elif turns["worker"] == 2:
                assert turns["consultation"] == 1
                assert any(m["role"] == "tool" and "请向用户确认" in m["content"] for m in kw["messages"])
                yield {"type": "done", "content": "", "tool_calls": [call("ask_user", {
                    "question": "这份简报主要给谁看？",
                }, "escalation-user")]}
            else:
                assert any(m["role"] == "tool" and "产品经理" in m["content"] for m in kw["messages"])
                yield {"type": "done", "content": "我自行组织成适合产品经理的简报。", "tool_calls": []}

    monkeypatch.setattr(orchestrator, "stream_chat", fake_stream_chat)
    run_id = client.post(f"/api/chats/{chat_id}/messages", json={"content": "整理简报。"}).json()["run"]["id"]
    run_worker(run_id)
    waiting = client.get(f"/api/runs/{run_id}").json()
    assert waiting["status"] == "waiting" and waiting["resume"]["kind"] == "question"
    assert turns["consultation"] == 1
    question_id = waiting["resume"]["question_id"]
    assert client.post(f"/api/questions/{question_id}/answer", json={"answer": "给产品经理看。"}).status_code == 200
    run_worker(run_id)
    assert client.get(f"/api/runs/{run_id}").json()["status"] == "completed"
    assert turns["consultation"] == 1 and turns["worker"] == 3
