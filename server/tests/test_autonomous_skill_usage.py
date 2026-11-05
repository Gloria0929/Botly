"""是否使用技能由模型自主决定，按需加载可复用并保留真实证据。"""
from __future__ import annotations

import asyncio
import json
from uuid import uuid4

import pytest
from sqlalchemy import select

from app.agent import orchestrator
from app.agent.orchestrator import RunExecutor
from app.agent.skill_usage import usage_state
from app.core.db import SessionLocal
from app.core.models import Message, Run, Skill, Task, Thread
from app.sandbox import files
from app.tools import skills as skillmod
from app.worker import process_one
from tests.test_run_reliability import _new_run
from tests.conftest import register


@pytest.fixture
def workspace(client, invite):
    skills = []

    def prepare(client, bots):
        for name, description, prompt in [
            ("页面设计方法", "用于界面布局、视觉层级和交互设计", "视觉要求：使用清晰层级和一致间距。"),
            ("代码实现方法", "实现和修改软件代码", "编码要求：使用现有技术栈并验证实际行为。"),
            ("数据统计方法", "统计分析与数据可视化", "不相关的统计分析正文。"),
        ]:
            skills.append(client.post("/api/skills", json={"name": name, "description": description, "prompt": prompt}).json())
        for bot in bots:
            client.patch(f"/api/bots/{bot['id']}", json={"skills": [s["id"] for s in skills]})

    ctx = _new_run(client, invite, f"autonomous-skills-{uuid4().hex[:10]}@test.dev", prepare=prepare)
    # 此 fixture 每个用例独立注册，避免共享数据库的账号冲突。
    ctx["skills"] = skills
    return ctx


def call(name, args, cid):
    return {"id": cid, "type": "function", "function": {"name": name, "arguments": json.dumps(args, ensure_ascii=False)}}


def store_call(ctx, tool_call, *, bot_id=None, thread_id=None, interjection_seq=0):
    with SessionLocal() as db:
        msg = orchestrator.add_message(db, thread=db.get(Thread, thread_id or ctx["main_id"]),
            run_id=ctx["run_id"], sender_type="bot", sender_bot_id=bot_id or ctx["supervisor_id"],
            type_="tool_call", meta={"tool_calls": [tool_call], "interjection_seq": interjection_seq})
        db.commit()
        return msg.id


def execute(ctx, tool_call, *, store=True, bot_id=None, thread_id=None, interjection_seq=0):
    bot_id = bot_id or ctx["supervisor_id"]
    thread_id = thread_id or ctx["main_id"]
    if store:
        store_call(ctx, tool_call, bot_id=bot_id, thread_id=thread_id, interjection_seq=interjection_seq)
    ex = RunExecutor(ctx["run_id"])
    result = asyncio.run(ex._handle_tool_call(thread_id=thread_id, bot_id=bot_id,
                                            task_id=None, call=tool_call, is_supervisor=True))
    ex.write_tool_result(thread_id, tool_call, result)
    return result


@pytest.mark.parametrize("use_skills", [False, True])
def test_worker_can_choose_skills_or_execute_directly(client, workspace, monkeypatch, use_skills):
    ctx = workspace
    turns = []
    supervisor_turns = []

    async def stream(**kw):
        if kw["ctx"]["role"] == "supervisor":
            supervisor_turns.append(kw)
            calls = [call("delegate", {"bot": "研究员", "instruction": "实现一个界面，完成布局和代码"}, "delegate-ui")] if len(supervisor_turns) == 1 else []
            yield {"type": "done", "content": "已汇总" if not calls else "", "tool_calls": calls}
            return
        turns.append(kw)
        assert "skill_select" in {t["function"]["name"] for t in kw["tools"]}
        body = json.dumps(kw["messages"], ensure_ascii=False)
        if len(turns) == 1:
            assert "本成员在本线程已加载：无" in body
            assert "不需要技能时直接回答或调用业务工具" in body
            assert all(s["prompt"] not in body for s in ctx["skills"])
            calls = [call("skill_select", {"skills": ["页面设计方法", "代码实现方法"]}, "select-ui")] if use_skills else [
                call("file_write", {"path": "output/ui.txt", "content": "直接完成任务"}, "write-ui")]
        elif use_skills and len(turns) == 2:
            assert ctx["skills"][0]["prompt"] in body and ctx["skills"][1]["prompt"] in body
            assert ctx["skills"][2]["prompt"] not in body
            calls = [call("file_write", {"path": "output/ui.txt", "content": "已按设计与实现方法完成"}, "write-ui")]
        else:
            assert (files.chat_root(ctx["user_id"], ctx["chat_id"]) / "output/ui.txt").exists()
            yield {"type": "done", "content": "已完成并验证 output/ui.txt", "tool_calls": []}
            return
        yield {"type": "done", "content": "", "tool_calls": calls}

    monkeypatch.setattr(orchestrator, "stream_chat", stream)
    asyncio.run(process_one(ctx["run_id"]))
    with SessionLocal() as db:
        assert db.get(Run, ctx["run_id"]).status == "completed"
        assert db.scalar(select(Task).where(Task.run_id == ctx["run_id"])).status == "completed"
        evidence = [m.meta["skill_usage"] for m in db.scalars(select(Message).where(Message.run_id == ctx["run_id"]))
                    if m.meta.get("skill_usage")]
        assert len(evidence) == int(use_skills)
        if use_skills:
            assert evidence[0]["loaded"] == [s["id"] for s in ctx["skills"][:2]]
    assert len(turns) == (3 if use_skills else 2)


def test_plain_question_does_not_force_skill_calls(client, workspace, monkeypatch):
    ctx = workspace
    turns = []
    async def stream(**kw):
        turns.append(kw)
        yield {"type": "done", "content": "当前启用了 3 个技能。", "tool_calls": []}
    monkeypatch.setattr(orchestrator, "stream_chat", stream)
    asyncio.run(process_one(ctx["run_id"]))
    assert len(turns) == 1
    assert all(skill["prompt"] not in json.dumps(turns[0]["messages"], ensure_ascii=False) for skill in ctx["skills"])


def test_new_user_input_does_not_require_selection_or_a_skip_reason(workspace):
    ctx = workspace
    for seq, name in [(0, "original"), (30, "new-goal")]:
        execute(ctx, call("file_write", {"path": f"output/{name}.txt", "content": name}, name), interjection_seq=seq)
        assert files.read_file(ctx["user_id"], ctx["chat_id"], f"output/{name}.txt") == name
    with SessionLocal() as db:
        results = db.scalars(select(Message).where(Message.run_id == ctx["run_id"], Message.type == "tool_result"))
        assert not any(m.meta.get("skill_usage") for m in results)


def test_empty_optional_selection_needs_no_explanation(workspace):
    ctx = workspace
    result = execute(ctx, call("skill_select", {"skills": []}, "empty"))
    assert "可直接按当前目标继续" in result
    assert all(s["prompt"] not in result for s in ctx["skills"])


def test_successful_read_and_selection_survive_replay_without_reloading(workspace):
    ctx = workspace
    initial = call("skill_file_read", {"path": "页面设计方法/SKILL.md"}, "read")
    first = execute(ctx, initial)
    assert ctx["skills"][0]["prompt"] in first
    assert execute(ctx, initial, store=False) == first
    repeated = execute(ctx, call("skill_select", {"skills": ["页面设计方法", "代码实现方法"], "reason": "继续实现阶段"}, "next-stage"))
    assert "已在本线程加载" in repeated
    assert ctx["skills"][0]["prompt"] not in repeated and ctx["skills"][1]["prompt"] in repeated
    with SessionLocal() as db:
        reads = db.scalars(select(Message).where(Message.run_id == ctx["run_id"], Message.type == "tool_result",
            Message.meta["tool_call_id"].as_string() == "read")).all()
        assert len(reads) == 1


@pytest.mark.parametrize("bad", ["unbound", "disabled", "foreign", "missing", "reference"])
def test_invalid_selection_or_reference_is_not_recorded_as_loaded(client, invite, workspace, bad):
    ctx = workspace
    name = "页面设计方法"
    foreign = register(client, invite, f"foreign-skill-{uuid4().hex[:10]}@test.dev") if bad == "foreign" else None
    with SessionLocal() as db:
        row = db.get(Skill, ctx["skills"][0]["id"])
        if bad == "unbound":
            row = Skill(user_id=ctx["user_id"], name="未绑定技能", description="测试", prompt="不应加载")
            db.add(row)
            name = row.name
        elif bad == "disabled":
            row.enabled = False
        elif bad == "foreign":
            row.user_id = foreign["id"]
        elif bad == "reference":
            row.files = skillmod.save_package(ctx["user_id"], row.id, {"notes.md": b"reference only"})
        elif bad == "missing":
            name = "不存在的技能"
        db.commit()
    tool_call = call("skill_file_read", {"path": "页面设计方法/notes.md"}, "reference") if bad == "reference" else call(
        "skill_select", {"skills": ["代码实现方法", name]}, "invalid-select")
    result = execute(ctx, tool_call)
    assert all(s["prompt"] not in result for s in ctx["skills"])
    with SessionLocal() as db:
        results = list(db.scalars(select(Message).where(Message.run_id == ctx["run_id"], Message.type == "tool_result")))
        assert not any(m.meta.get("skill_usage") for m in results)
    # 技能不可用也不会阻塞正常业务工具。
    execute(ctx, call("file_write", {"path": "output/continued.txt", "content": "继续处理"}, "write"))
    assert files.read_file(ctx["user_id"], ctx["chat_id"], "output/continued.txt") == "继续处理"


def test_skill_decisions_are_isolated_by_member_thread_and_run(workspace):
    ctx = workspace
    execute(ctx, call("skill_select", {"skills": ["代码实现方法"], "reason": "实现代码"}, "select"))
    other_bot = ctx["bots"]["研究员"]["id"]
    with SessionLocal() as db:
        thread = Thread(user_id=ctx["user_id"], chat_id=ctx["chat_id"], type="task", parent_thread_id=ctx["main_id"])
        db.add(thread)
        db.commit()
        thread_id = thread.id
        skills = list(db.scalars(select(Skill).where(Skill.user_id == ctx["user_id"])))
        assert not usage_state(db, "other-run", ctx["main_id"], ctx["supervisor_id"], skills).loaded_ids
        assert not usage_state(db, ctx["run_id"], ctx["main_id"], other_bot, skills).loaded_ids
        assert not usage_state(db, ctx["run_id"], thread_id, ctx["supervisor_id"], skills).loaded_ids
        assert usage_state(db, ctx["run_id"], ctx["main_id"], ctx["supervisor_id"], skills).loaded_ids == {ctx["skills"][1]["id"]}
