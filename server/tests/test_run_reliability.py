"""3.6 改派上限用稳定 step 键；3.8 崩溃重放不重复计步、计费或产生重复消息。"""
from __future__ import annotations

import asyncio
import json

import pytest

from sqlalchemy import func, select

from app.core.db import SessionLocal
from app.core.models import Chat, FileRec, Message, Run, Task, Thread, UsageLog
from app.agent.orchestrator import RunExecutor, _step_key, add_message
from tests.conftest import register, setup_workspace
from tests.test_parallel import drain_run, run_worker


def _new_run(client, invite, email, prepare=None):
    """注册 → 配置 mock 模型 → 建一次执行，返回上下文。"""
    register(client, invite, email)
    chat_id, bots = setup_workspace(client)
    if prepare:
        prepare(client, bots)
    run_id = client.post(f"/api/chats/{chat_id}/messages", json={"content": "帮我整理资料"}).json()["run"]["id"]
    with SessionLocal() as db:
        run = db.get(Run, run_id)
        chat = db.get(Chat, chat_id)
        main = db.scalar(select(Thread).where(Thread.chat_id == chat_id, Thread.type == "main"))
        return {
            "chat_id": chat_id,
            "run_id": run_id,
            "user_id": run.user_id,
            "supervisor_id": chat.supervisor_bot_id,
            "main_id": main.id,
            "bots": {b["name"]: b for b in bots},
        }


def _snapshot(run_id: str) -> dict:
    with SessionLocal() as db:
        return {
            "messages": db.scalar(select(func.count(Message.id)).where(Message.run_id == run_id)),
            "steps": db.get(Run, run_id).steps_used,
            "usage": db.scalar(select(func.count(UsageLog.id)).where(UsageLog.run_id == run_id)),
            "marker": db.get(Run, run_id).last_turn_message_id,
        }


def test_tool_turn_replay_reuses_committed_output(client, invite):
    """模型节点在「DB 已提交、检查点未写入」时被重放：不能再调一次模型。"""
    ctx = _new_run(client, invite, "replay-tool@test.dev")
    args = dict(
        role="supervisor",
        bot_id=ctx["supervisor_id"],
        thread_id=ctx["main_id"],
        task_id=None,
    )

    first = asyncio.run(RunExecutor(ctx["run_id"]).model_turn(**args))
    assert first == (True, "")
    before = _snapshot(ctx["run_id"])
    assert before["marker"]
    assert before["messages"] >= 2  # 触发消息 + 工具调用消息

    second = asyncio.run(RunExecutor(ctx["run_id"]).model_turn(**args))
    assert second == first
    assert _snapshot(ctx["run_id"]) == before


def test_text_turn_replay_reuses_committed_output(client, invite):
    def drop_tools(client, bots):
        # 没有工具的 Bot：mock 直接给文本，正好覆盖「无工具调用」这一路的落库
        researcher = next(b for b in bots if b["name"] == "研究员")
        client.patch(f"/api/bots/{researcher['id']}", json={"tools": []})

    ctx = _new_run(client, invite, "replay-text@test.dev", prepare=drop_tools)
    args = dict(
        role="worker",
        bot_id=ctx["bots"]["研究员"]["id"],
        thread_id=ctx["main_id"],
        task_id=None,
    )

    first = asyncio.run(RunExecutor(ctx["run_id"]).model_turn(**args))
    assert first[0] is False and first[1]
    before = _snapshot(ctx["run_id"])

    # 重放拿到同样的文本；不新增消息、不重复计步、不重复记用量
    second = asyncio.run(RunExecutor(ctx["run_id"]).model_turn(**args))
    assert second == first
    assert _snapshot(ctx["run_id"]) == before


def test_stale_streaming_draft_is_cleaned_before_new_turn(client, invite):
    ctx = _new_run(client, invite, "replay-stale@test.dev")
    with SessionLocal() as db:
        stale = add_message(
            db,
            thread=db.get(Thread, ctx["main_id"]),
            run_id=ctx["run_id"],
            sender_type="bot",
            sender_bot_id=ctx["supervisor_id"],
            type_="text",
            content="",
            status="streaming",
        )
        db.commit()
        stale_id = stale.id

    asyncio.run(RunExecutor(ctx["run_id"]).model_turn(
        role="supervisor",
        bot_id=ctx["supervisor_id"],
        thread_id=ctx["main_id"],
        task_id=None,
    ))
    with SessionLocal() as db:
        assert db.get(Message, stale_id) is None


def _delegate(ctx, *, instruction: str, step: str | None, call_id: str) -> dict:
    args = {"bot": "研究员", "instruction": instruction, "expected_output": "要点清单"}
    if step is not None:
        args["step"] = step
    call = {
        "id": call_id,
        "type": "function",
        "function": {"name": "delegate", "arguments": json.dumps(args, ensure_ascii=False)},
    }
    return asyncio.run(
        RunExecutor(ctx["run_id"]).start_delegate(
            thread_id=ctx["main_id"],
            bot_id=ctx["supervisor_id"],
            task_id=None,
            call=call,
        )
    )


@pytest.mark.parametrize("revision", [False, True])
@pytest.mark.parametrize("parallel", [False, True])
def test_duplicate_delegate_reuses_completed_work_but_explicit_revision_runs(
    client, invite, monkeypatch, revision, parallel,
):
    from app.agent import orchestrator

    ctx = _new_run(client, invite, f"duplicate-delegate-{revision}-{parallel}@test.dev")
    supervisor_turns = 0
    worker_turns = 0

    async def respond(**kw):
        nonlocal supervisor_turns, worker_turns
        if kw["ctx"]["role"] == "worker":
            if kw["ctx"]["bot_name"] == "研究员":
                worker_turns += 1
            yield {"type": "done", "content": "要点已整理，依据已核对", "tool_calls": []}
            return
        supervisor_turns += 1
        if parallel and supervisor_turns == 1:
            yield {"type": "done", "content": "", "tool_calls": [{"id": "duplicate-plan", "type": "function",
                "function": {"name": "propose_plan", "arguments": json.dumps({"steps": [
                    {"title": "资料收集", "bot": "研究员", "expected": "要点清单", "depends_on": []},
                    {"title": "独立复核", "bot": "架构师", "depends_on": []},
                ]}, ensure_ascii=False)}}]}
        elif supervisor_turns == (2 if parallel else 1) or (parallel and supervisor_turns == 3):
            assignments = [
                ("initial-work", "整理资料"),
                ("repeat-work", "只补充价格依据，复用现有资料" if revision else "整理资料"),
            ]
            if parallel:
                assignments = [assignments[0 if supervisor_turns == 2 else 1]]
            calls = [{"id": cid, "type": "function", "function": {"name": "delegate", "arguments": json.dumps({
                "bot": "研究员", "instruction": instruction, "step": "资料收集", "expected_output": "要点清单",
            }, ensure_ascii=False)}} for cid, instruction in assignments]
            yield {"type": "done", "content": "", "tool_calls": calls}
        else:
            results = [m for m in kw["messages"] if m["role"] == "tool" and m["tool_call_id"] != "duplicate-plan"]
            assert {m["tool_call_id"] for m in results} == {"initial-work", "repeat-work"}
            assert all("要点已整理" in m["content"] for m in results)
            yield {"type": "done", "content": "已验收并汇总", "tool_calls": []}

    monkeypatch.setattr(orchestrator, "stream_chat", respond)
    if parallel:
        run_worker(ctx["run_id"])
        assert client.post(f"/api/runs/{ctx['run_id']}/plan/approve", json={}).status_code == 200
    assert drain_run(client, ctx["run_id"])["status"] == "completed"
    assert worker_turns == (2 if revision else 1)
    with SessionLocal() as db:
        tasks = db.scalars(select(Task).where(Task.run_id == ctx["run_id"])).all()
        assert len(tasks) == worker_turns + int(parallel) and all(t.status == "completed" for t in tasks)


def test_duplicate_active_delegate_resumes_same_task_and_answers_both_calls(client, invite):
    ctx = _new_run(client, invite, "duplicate-active@test.dev")
    for cid in ("active-first", "active-again"):
        with SessionLocal() as db:
            add_message(db, thread=db.get(Thread, ctx["main_id"]), run_id=ctx["run_id"],
                        sender_type="bot", sender_bot_id=ctx["supervisor_id"], type_="tool_call",
                        meta={"tool_calls": [{"id": cid, "type": "function", "function": {
                            "name": "delegate", "arguments": json.dumps({"bot": "研究员", "instruction": "整理资料",
                                                                         "step": "资料收集"}, ensure_ascii=False),
                        }}]})
            db.commit()
        patch = _delegate(ctx, instruction="整理资料", step="资料收集", call_id=cid)
        if cid == "active-first":
            task_id = patch["task_id"]
        assert patch["task_id"] == task_id
    asyncio.run(RunExecutor(ctx["run_id"]).finish_task(task_id=task_id, content="原任务结果已完成"))
    with SessionLocal() as db:
        assert db.scalar(select(func.count(Task.id)).where(Task.run_id == ctx["run_id"])) == 1
        results = db.scalars(select(Message).where(Message.thread_id == ctx["main_id"], Message.type == "tool_result")).all()
        assert {(m.meta or {})["tool_call_id"] for m in results} == {"active-first", "active-again"}
        assert all("原任务结果已完成" in m.content for m in results)


def test_parallel_handoff_keeps_expected_output_artifact_and_validation_details(client, invite):
    ctx = _new_run(client, invite, "parallel-handoff@test.dev")
    with SessionLocal() as db:
        task = Task(user_id=ctx["user_id"], run_id=ctx["run_id"], thread_id=ctx["main_id"],
                    assigned_bot_id=ctx["bots"]["研究员"]["id"], instruction="整理厂商资料", step_key="资料收集",
                    expected_output="附来源的厂商对比报告", batch=1, status="completed",
                    result="已整理来源。" * 100 + "交付 report.md；已核对三家官网，仍需用户确认报价税率。")
        db.add(task)
        db.add(FileRec(user_id=ctx["user_id"], chat_id=ctx["chat_id"], run_id=ctx["run_id"], path="report.md",
                       created_by_bot_id=ctx["bots"]["研究员"]["id"], size=600))
        db.flush()
        RunExecutor(ctx["run_id"])._maybe_write_batch_summary(db, db.get(Thread, ctx["main_id"]), task)
        msg = db.scalar(select(Message).where(Message.run_id == ctx["run_id"], Message.type == "system"))
        assert "附来源的厂商对比报告" in msg.content
        assert "相关产物记录：report.md" in msg.content
        assert "仍需用户确认报价税率" in msg.content
        assert "完成状态不代表已经验收通过" in msg.content


def test_reassign_limit_counts_same_step_across_rephrasing(client, invite):
    ctx = _new_run(client, invite, "reassign@test.dev")
    with SessionLocal() as db:
        db.add(
            Task(
                user_id=ctx["user_id"],
                run_id=ctx["run_id"],
                thread_id=ctx["main_id"],
                assigned_bot_id=ctx["bots"]["研究员"]["id"],
                instruction="调查三家国内向量数据库",
                step_key=_step_key("资料收集"),
                status="failed",
                attempt=1,
            )
        )
        db.commit()

    # 第一次改派：换了说法、换了角度，但 step 没变 —— 仍算同一个子任务
    patch = _delegate(ctx, instruction="换个角度再查一遍资料", step="资料收集", call_id="call-a")
    assert patch.get("task_id")
    with SessionLocal() as db:
        retried = db.get(Task, patch["task_id"])
        assert retried.attempt == 2
        retried.status = "failed"
        db.commit()

    # 第二次改派：超过上限，直接被拦，不再新建任务
    blocked = _delegate(ctx, instruction="第三次，再换一种说法", step=" 资料收集 ", call_id="call-b")
    assert blocked == {}
    with SessionLocal() as db:
        tasks = db.scalars(select(Task).where(Task.run_id == ctx["run_id"])).all()
        assert len(tasks) == 2
        results = [
            m.content
            for m in db.scalars(
                select(Message).where(
                    Message.run_id == ctx["run_id"], Message.type == "tool_result"
                )
            ).all()
        ]
    assert any("改派次数上限" in r for r in results)
