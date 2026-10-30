"""端到端验收测试：直接驱动编排器，不需要常驻 Worker 进程。

对应需求文档第 13 节的验收项：
1  计划卡片 → 派给两个 Bot → 汇总
2  账号隔离（越权返回 404）
3  邀请码无效 / 已用完
4  审批暂停与批准后继续
5  停止
6  超限自动停止
7  超出步数上限
8  @Bot 直连
9  沙箱文件产物 + 路径穿越被拒
"""
from __future__ import annotations

import asyncio

import pytest

from app.worker import process_one
from tests.conftest import login, register, setup_workspace


def run_worker(run_id: str) -> None:
    """同步地跑一次 Worker 认领 + 执行。"""
    asyncio.run(process_one(run_id))


def wait_status(client, run_id: str, expect: set[str]) -> dict:
    run = client.get(f"/api/runs/{run_id}").json()
    assert run["status"] in expect, f"期望 {expect}，实际 {run['status']}（{run.get('end_reason')}）"
    return run


def messages(client, chat_id: str) -> list[dict]:
    return client.get(f"/api/chats/{chat_id}/messages?limit=200").json()["messages"]


def build_main_history(run_id: str, chat_id: str) -> list[dict]:
    """重建总指挥这一轮真正发给模型的消息。"""
    from sqlalchemy import select

    from app.db import SessionLocal
    from app.models import Bot, Chat, Run, Thread
    from app.orchestrator import RunExecutor

    with SessionLocal() as db:
        run = db.get(Run, run_id)
        chat = db.get(Chat, chat_id)
        thread = db.scalar(
            select(Thread).where(Thread.chat_id == chat_id, Thread.type == "main")
        )
        sup = db.get(Bot, chat.supervisor_bot_id)
        return RunExecutor(run_id)._build_messages(db, run, chat, thread, sup, True, [sup])


def assert_tool_history_valid(built: list[dict]) -> None:
    """assistant(tool_calls) 后面必须紧跟每个 tool_call_id 对应的 tool 消息。"""
    for i, m in enumerate(built):
        if m.get("role") == "assistant" and m.get("tool_calls"):
            ids = {c["id"] for c in m["tool_calls"]}
            following: set[str] = set()
            j = i + 1
            while j < len(built) and built[j]["role"] == "tool":
                following.add(built[j]["tool_call_id"])
                j += 1
            assert following == ids, f"tool_calls 未被完整回答：{ids} vs {following}"


@pytest.mark.parametrize("mention_supervisor", [False, True])
def test_full_flow_plan_delegate_summarize(client, invite, mention_supervisor):
    register(client, invite, f"flow-{mention_supervisor}@test.dev")
    chat_id, bots = setup_workspace(client)
    supervisor = next(b for b in bots if b["role"] == "总指挥")
    prefix = f"@{supervisor['name']} " if mention_supervisor else ""

    r = client.post(
        f"/api/chats/{chat_id}/messages",
        json={"content": prefix + "调研 3 家国内向量数据库厂商，写一份对比报告。"},
    )
    assert r.status_code == 201, r.text
    run_id = r.json()["run"]["id"]

    # 第一次执行：总指挥发计划后暂停
    run_worker(run_id)
    run = wait_status(client, run_id, {"waiting"})
    assert run["resume"]["kind"] == "plan"
    assert run["target_bot_id"] == (supervisor["id"] if mention_supervisor else None)
    assert len(run["resume"]["plan"]) == 2

    plan_msg = next(m for m in messages(client, chat_id) if m["type"] == "plan")
    assert plan_msg["meta"]["status"] == "pending"

    # 批准计划 → 继续执行到完成
    assert client.post(f"/api/runs/{run_id}/plan/approve", json={}).status_code == 200
    run_worker(run_id)
    run = wait_status(client, run_id, {"completed"})

    msgs = messages(client, chat_id)
    types = [m["type"] for m in msgs]
    assert "plan" in types

    # 派活是群聊里的 @ 消息，子 Bot 以自己的身份回复
    delegations = [m for m in msgs if (m["meta"] or {}).get("delegation")]
    replies = [m for m in msgs if (m["meta"] or {}).get("subtask")]
    assert len(delegations) == 2, "应派给两个不同的 Bot"
    assert len(replies) == 2, "两个子 Bot 都应回话"
    # 子 Bot 的最终回复已经通过群聊镜像实时发出；完成后应升级为
    # 完成消息，不应再追加一条重复回复，也不应残留 subtask_live。
    assert not [m for m in msgs if (m["meta"] or {}).get("subtask_live")]
    assert len({m["content"] for m in replies}) == 2, "同一条回复不应出现两次"
    assert all(m["content"].startswith("@") for m in delegations)
    assert {m["meta"]["mention_name"] for m in delegations} == {"研究员", "写手"}
    assert all(m["sender_type"] == "bot" and m["content"] for m in replies)

    files = client.get(f"/api/chats/{chat_id}/files").json()
    assert {f["path"] for f in files} >= {"research.md", "report.md"}
    assert run["tokens_used"] > 0


def test_data_isolation(client, invite):
    register(client, invite, "alice@test.dev")
    chat_id, _ = setup_workspace(client)
    provider_id = client.get("/api/providers").json()[0]["id"]
    bot_id = client.get("/api/bots").json()[0]["id"]

    register(client, invite, "bob@test.dev")
    for path in (
        f"/api/chats/{chat_id}",
        f"/api/chats/{chat_id}/messages",
        f"/api/bots/{bot_id}",
    ):
        assert client.get(path).status_code == 404, path
    assert client.patch(f"/api/providers/{provider_id}", json={"name": "hack"}).status_code == 404

    # 新账号会被自动创建默认 Bot 与群聊，但不应看到对方的任何数据
    assert all(c["id"] != chat_id for c in client.get("/api/chats").json())
    assert all(b["id"] != bot_id for b in client.get("/api/bots").json())

    login(client, "alice@test.dev")
    assert client.get(f"/api/chats/{chat_id}").status_code == 200


def test_invalid_and_exhausted_invite(client, invite):
    r = client.post(
        "/api/auth/register",
        json={"email": "x1@test.dev", "password": "password-123", "invite_code": "NOPE-0000"},
    )
    assert r.status_code == 400 and "无效" in r.json()["detail"]

    from app.db import SessionLocal
    from app.models import InviteCode

    with SessionLocal() as db:
        db.add(InviteCode(code="USED-UP-0001", max_uses=1, used_count=1))
        db.commit()

    r = client.post(
        "/api/auth/register",
        json={"email": "x2@test.dev", "password": "password-123", "invite_code": "USED-UP-0001"},
    )
    assert r.status_code == 400 and "用完" in r.json()["detail"]


def test_approval_pause_and_resume(client, invite):
    register(client, invite, "approval@test.dev")
    chat_id, _ = setup_workspace(client)
    # 把 L2 的 file_write 改为需要审批
    assert client.patch(f"/api/chats/{chat_id}", json={"require_approval_for_write": True}).status_code == 200

    run_id = client.post(
        f"/api/chats/{chat_id}/messages", json={"content": "整理一份资料并写成文件。"}
    ).json()["run"]["id"]

    run_worker(run_id)
    wait_status(client, run_id, {"waiting"})  # plan
    client.post(f"/api/runs/{run_id}/plan/approve", json={})

    run_worker(run_id)
    run = wait_status(client, run_id, {"waiting"})
    assert run["resume"]["kind"] == "approval"

    approvals = client.get("/api/approvals").json()
    pending = [a for a in approvals if a["status"] == "pending"]
    assert pending and pending[0]["tool"] == "file_write"
    assert pending[0]["level"] == "L2"

    # 拒绝：原因要回传给 Bot，且任务不能卡死
    assert client.post(f"/api/approvals/{pending[0]['id']}/reject", json={"reason": "内容不符"}).status_code == 200
    run_worker(run_id)
    run = wait_status(client, run_id, {"completed", "waiting", "failed"})
    if run["status"] == "waiting":
        assert run["resume"]["kind"] == "approval", "后续仍需审批"
    rejected = [a for a in client.get("/api/approvals").json() if a["id"] == pending[0]["id"]][0]
    assert rejected["status"] == "rejected"


def test_stop_run(client, invite):
    register(client, invite, "stop@test.dev")
    chat_id, _ = setup_workspace(client)
    run_id = client.post(
        f"/api/chats/{chat_id}/messages", json={"content": "写一份长报告。"}
    ).json()["run"]["id"]

    r = client.post(f"/api/runs/{run_id}/stop")
    assert r.status_code == 200
    assert r.json()["status"] == "stopped"

    # 已停止的执行不能再继续
    assert client.post(f"/api/runs/{run_id}/continue", json={}).status_code == 400


def test_old_step_setting_no_longer_stops_run(client, invite):
    register(client, invite, "limit@test.dev")
    chat_id, _ = setup_workspace(client)
    assert client.patch("/api/settings", json={"max_steps": 1}).json()["max_steps"] == 0
    run_id = client.post(f"/api/chats/{chat_id}/messages", json={"content": "做个复杂任务。"}).json()["run"]["id"]
    run_worker(run_id)
    wait_status(client, run_id, {"waiting"})
    client.post(f"/api/runs/{run_id}/plan/approve", json={})
    run_worker(run_id)
    run = wait_status(client, run_id, {"completed"})
    assert run["steps_used"] > 1


def test_delete_bot_removes_card_but_keeps_history(client, invite):
    """BOT-03：删除后管理列表不再出现，历史消息保留并标为「已删除」。"""
    register(client, invite, "deletebot@test.dev")
    chat_id, bots = setup_workspace(client)
    researcher = next(b for b in bots if b["name"] == "研究员")

    run_id = client.post(
        f"/api/chats/{chat_id}/messages",
        json={"content": "调研 3 家国内向量数据库厂商，写一份对比报告。"},
    ).json()["run"]["id"]
    run_worker(run_id)
    wait_status(client, run_id, {"waiting"})
    client.post(f"/api/runs/{run_id}/plan/approve", json={})
    run_worker(run_id)
    wait_status(client, run_id, {"completed"})

    assert client.delete(f"/api/bots/{researcher['id']}").status_code == 200

    # 默认列表不再返回已删除的 Bot，显式查询才能拿到
    assert researcher["id"] not in {b["id"] for b in client.get("/api/bots").json()}
    assert researcher["id"] in {
        b["id"] for b in client.get("/api/bots?include_deleted=true").json()
    }

    # 群聊成员同步移除
    assert researcher["id"] not in {
        m["id"] for m in client.get(f"/api/chats/{chat_id}").json()["members"]
    }

    # 历史消息保留，并带出发送者名字与已删除标记
    from_bot = [
        m
        for m in messages(client, chat_id)
        if m["sender_bot_id"] == researcher["id"] and m["type"] == "text"
    ]
    assert from_bot, "历史消息不应随 Bot 删除消失"
    assert from_bot[0]["sender_bot_name"] == "研究员（已删除）"
    assert from_bot[0]["sender_bot_deleted"] is True


def test_soft_delete_releases_bot_name(client, invite):
    """删除后名字可复用，但活着的同名仍会冲突。"""
    register(client, invite, "namereuse@test.dev")
    _, bots = setup_workspace(client)
    researcher = next(b for b in bots if b["name"] == "研究员")
    writer = next(b for b in bots if b["name"] == "写手")

    assert client.delete(f"/api/bots/{researcher['id']}").status_code == 200

    # 已删除的名字可以复用：改名不再报重名
    r = client.patch(f"/api/bots/{writer['id']}", json={"name": "研究员"})
    assert r.status_code == 200, r.text
    assert r.json()["name"] == "研究员"

    # 但活着的同名仍然冲突
    r = client.post("/api/bots", json={"name": "研究员", "avatar": "🔍", "role": "研究员"})
    assert r.status_code == 400 and "已存在" in r.json()["detail"]


def test_reasoning_content_is_echoed_back(client, invite):
    """推理模型的 reasoning_content 必须在下一轮原样回传，且不下发前端。"""
    from sqlalchemy import select

    from app.db import SessionLocal
    from app.models import Bot, Chat, Thread
    from app.orchestrator import add_message

    register(client, invite, "reasoning@test.dev")
    chat_id, _ = setup_workspace(client)
    run_id = client.post(
        f"/api/chats/{chat_id}/messages", json={"content": "思考一下再回答"}
    ).json()["run"]["id"]

    with SessionLocal() as db:
        chat = db.get(Chat, chat_id)
        thread = db.scalar(
            select(Thread).where(Thread.chat_id == chat_id, Thread.type == "main")
        )
        sup = db.get(Bot, chat.supervisor_bot_id)
        m = add_message(
            db,
            thread=thread,
            run_id=run_id,
            sender_type="bot",
            type_="text",
            content="这是最终回答",
            sender_bot_id=sup.id,
        )
        m.reasoning_content = "这是思考过程"
        db.commit()

    # 构造给模型的上下文：assistant 消息必须带 reasoning_content
    built = build_main_history(run_id, chat_id)
    assistant = [x for x in built if x.get("content") == "这是最终回答"]
    assert assistant and assistant[0].get("reasoning_content") == "这是思考过程"

    # 前端拉取消息不应拿到思维链（避免大体积 + 泄露）
    raw = next(
        m for m in messages(client, chat_id) if m["content"] == "这是最终回答"
    )
    assert "reasoning_content" not in raw


def test_reasoning_content_on_all_tool_calls_of_a_turn(client, invite, monkeypatch):
    """一轮返回多个 tool_call 时，每条都要带思维链，否则下一轮接口报 400。"""
    from sqlalchemy import select

    from app import orchestrator
    from app.db import SessionLocal
    from app.models import Message

    register(client, invite, "multireason@test.dev")
    chat_id, bots = setup_workspace(client)
    researcher = next(b for b in bots if b["name"] == "研究员")
    client.patch(f"/api/bots/{researcher['id']}", json={"tools": ["file_list"]})

    calls = {"n": 0}

    async def fake_stream_chat(**kw):
        calls["n"] += 1
        if calls["n"] == 1:
            yield {
                "type": "done",
                "content": "",
                "reasoning_content": "先思考",
                "tool_calls": [
                    {"id": "c0", "type": "function", "function": {"name": "file_list", "arguments": "{}"}},
                    {"id": "c1", "type": "function", "function": {"name": "file_list", "arguments": "{}"}},
                ],
                "usage": {},
                "finish_reason": "tool_calls",
            }
        else:
            yield {"type": "done", "content": "完成", "reasoning_content": "", "tool_calls": [], "usage": {}, "finish_reason": "stop"}

    monkeypatch.setattr(orchestrator, "stream_chat", fake_stream_chat)

    run_id = client.post(
        f"/api/chats/{chat_id}/messages", json={"content": "@研究员 查一下"}
    ).json()["run"]["id"]
    run_worker(run_id)

    with SessionLocal() as db:
        tc = db.scalars(
            select(Message).where(Message.run_id == run_id, Message.type == "tool_call")
        ).all()
    assert len(tc) == 2, "应有两次 file_list 工具调用"
    assert all(m.reasoning_content == "先思考" for m in tc), [
        m.reasoning_content for m in tc
    ]


@pytest.mark.parametrize("reasoning", ["原始派活思考", ""])
def test_delegate_preserves_reasoning_and_passes_worker_instruction(client, invite, monkeypatch, reasoning):
    """派活展示消息不能破坏思考链，子 Bot 必须收到指令和期望产出。"""
    import json

    from app import orchestrator

    register(client, invite, f"delegate-reasoning-{'full' if reasoning else 'empty'}@test.dev")
    chat_id, bots = setup_workspace(client)
    researcher = next(b for b in bots if b["name"] == "研究员")
    instruction = "只设计登录模块，列出身份验证和会话失效规则。"
    expected = "登录模块需求清单"
    supervisor_turns = 0

    async def fake_stream_chat(**kw):
        nonlocal supervisor_turns
        if kw["ctx"]["role"] == "worker":
            assert any(
                m["role"] == "user" and instruction in m["content"] and expected in m["content"]
                for m in kw["messages"]
            ), "真实模型的上下文里必须有派活指令，不能只在 mock ctx 里传入"
            yield {"type": "done", "content": "登录规则已经整理", "reasoning_content": "子任务思考", "tool_calls": []}
            return
        supervisor_turns += 1
        if supervisor_turns == 1:
            yield {
                "type": "done", "content": "", "reasoning_content": reasoning,
                "tool_calls": [{
                    "id": "delegate-login", "type": "function",
                    "function": {"name": "delegate", "arguments": json.dumps({
                        "bot": researcher["name"], "instruction": instruction, "expected_output": expected,
                    }, ensure_ascii=False)},
                }],
            }
            return
        assert_tool_history_valid(kw["messages"])
        assistants = [m for m in kw["messages"] if m["role"] == "assistant"]
        assert len(assistants) == 1, "派活展示文本不能成为额外的 assistant 回复"
        assert assistants[0]["reasoning_content"] == reasoning
        assert any(m["role"] == "tool" and "登录规则已经整理" in m["content"] for m in kw["messages"])
        yield {"type": "done", "content": "这是登录需求", "reasoning_content": "汇总思考", "tool_calls": []}

    monkeypatch.setattr(orchestrator, "stream_chat", fake_stream_chat)
    run_id = client.post(
        f"/api/chats/{chat_id}/messages", json={"content": "帮我设计一个登录的需求"}
    ).json()["run"]["id"]
    run_worker(run_id)
    wait_status(client, run_id, {"completed"})
    assert supervisor_turns == 2
    assert any(m["content"] == "这是登录需求" for m in messages(client, chat_id))


def test_new_run_ignores_old_control_messages_and_keeps_named_bot_replies(client, invite):
    """新任务不能沿用旧计划、报错和预算；其他 Bot 的结果必须可见且署名。"""
    from sqlalchemy import select

    from app.db import SessionLocal
    from app.models import Bot, Chat, Run, Thread
    from app.orchestrator import add_message

    register(client, invite, "new-run-context@test.dev")
    chat_id, bots = setup_workspace(client)
    researcher = next(b for b in bots if b["name"] == "研究员")
    old_id = client.post(
        f"/api/chats/{chat_id}/messages", json={"content": "评审上一份简报"}
    ).json()["run"]["id"]
    stale_notes = ["用户已确认计划，继续评审简报", "模型调用失败：400", "已使用约 94% 的 token 预算"]
    with SessionLocal() as db:
        old = db.get(Run, old_id)
        chat = db.get(Chat, chat_id)
        thread = db.scalar(select(Thread).where(Thread.chat_id == chat_id, Thread.type == "main"))
        for content in stale_notes:
            add_message(db, thread=thread, run_id=old_id, sender_type="system", type_="system", content=content)
        add_message(db, thread=thread, run_id=old_id, sender_type="user", type_="text",
                    content="补充：继续旧简报评审", meta={"kind": "interjection"})
        # 旧版本的派活副本没有 delegation 标记，也没有 reasoning_content。
        add_message(db, thread=thread, run_id=old_id, sender_type="bot", type_="text",
                    sender_bot_id=chat.supervisor_bot_id, content="@研究员 继续旧评审",
                    meta={"task_id": "old-task", "mention_bot_id": researcher["id"]})
        add_message(db, thread=thread, run_id=old_id, sender_type="bot", type_="text",
                    sender_bot_id=researcher["id"], content="评审完成，发现一处价格错误",
                    meta={"subtask": True, "task_id": "old-task"})
        old.status = "completed"
        db.commit()

    run_id = client.post(
        f"/api/chats/{chat_id}/messages", json={"content": "帮我设计一个登录的需求"}
    ).json()["run"]["id"]
    built = build_main_history(run_id, chat_id)
    assert all(m["content"] not in stale_notes for m in built)
    assert not any("执行过程中补充" in m["content"] for m in built), "旧插话不能成为本轮的优先指令"
    assert not any(m["content"] == "@研究员 继续旧评审" for m in built)
    reply = next(m for m in built if "评审完成，发现一处价格错误" in m["content"])
    assert reply["role"] == "assistant" and "研究员" in reply["content"] and "参考资料" in reply["content"]
    assert [m["content"] for m in built if m["role"] == "user"] == ["帮我设计一个登录的需求"]
    assert built[-1] == {"role": "user", "content": "帮我设计一个登录的需求"}
    assert any("当前已使用 0" in m["content"] for m in built if m["role"] == "system")

    # 历史回复可以用作后续评审背景，但不能泄露后续新任务到旧执行。
    old_built = build_main_history(old_id, chat_id)
    assert not any(m["content"] == "帮我设计一个登录的需求" for m in old_built)


def test_current_request_survives_long_running_text_history(client, invite):
    """裁剪历史不能把本轮用户目标和插话挤掉。"""
    from sqlalchemy import select

    from app.db import SessionLocal
    from app.models import Chat, Thread
    from app.orchestrator import add_message

    register(client, invite, "long-current-context@test.dev")
    chat_id, _ = setup_workspace(client)
    run_id = client.post(
        f"/api/chats/{chat_id}/messages", json={"content": "本次只设计登录需求"}
    ).json()["run"]["id"]
    with SessionLocal() as db:
        chat = db.get(Chat, chat_id)
        thread = db.scalar(select(Thread).where(Thread.chat_id == chat_id, Thread.type == "main"))
        for i in range(45):
            m = add_message(db, thread=thread, run_id=run_id, sender_type="bot", type_="text",
                            sender_bot_id=chat.supervisor_bot_id, content=f"处理第 {i} 步")
            m.reasoning_content = f"第 {i} 步思考"
        db.commit()
    built = build_main_history(run_id, chat_id)
    assert {"role": "user", "content": "本次只设计登录需求"} in built


def test_multiple_delegates_keep_their_own_results(client, invite, monkeypatch):
    """同轮两次派活都要执行，第一项结果不能代替第二项结果。"""
    import json

    from app import orchestrator

    register(client, invite, "multiple-delegates@test.dev")
    chat_id, bots = setup_workspace(client)
    researcher = next(b for b in bots if b["name"] == "研究员")
    supervisor_turns = 0
    worker_instructions = []

    async def fake_stream_chat(**kw):
        nonlocal supervisor_turns
        if kw["ctx"]["role"] == "worker":
            instruction = kw["ctx"]["task"]
            worker_instructions.append(instruction)
            yield {"type": "done", "content": f"完成：{instruction}", "reasoning_content": "子任务思考", "tool_calls": []}
            return
        supervisor_turns += 1
        if supervisor_turns == 1:
            yield {
                "type": "done", "content": "", "reasoning_content": "两项任务的思考",
                "tool_calls": [{
                    "id": f"delegate-{i}", "type": "function",
                    "function": {"name": "delegate", "arguments": json.dumps({
                        "bot": researcher["name"], "instruction": instruction,
                    }, ensure_ascii=False)},
                } for i, instruction in enumerate(["登录规则", "退出规则"])],
            }
            return
        assert worker_instructions == ["登录规则", "退出规则"], "汇总前必须先执行完同轮的两次派活"
        results = {m["tool_call_id"]: m["content"] for m in kw["messages"] if m["role"] == "tool"}
        assert "完成：登录规则" in results["delegate-0"]
        assert "完成：退出规则" in results["delegate-1"]
        assert_tool_history_valid(kw["messages"])
        yield {"type": "done", "content": "登录和退出规则均完成", "reasoning_content": "汇总思考", "tool_calls": []}

    monkeypatch.setattr(orchestrator, "stream_chat", fake_stream_chat)
    run_id = client.post(
        f"/api/chats/{chat_id}/messages", json={"content": "设计登录和退出需求"}
    ).json()["run"]["id"]
    run_worker(run_id)
    wait_status(client, run_id, {"completed"})
    assert supervisor_turns == 2 and worker_instructions == ["登录规则", "退出规则"]


def test_worker_failure_returns_to_supervisor_for_final_reply(client, invite, monkeypatch):
    """子 Bot 失败后仍需总指挥读取失败结果并回复用户。"""
    import json

    from app import orchestrator
    from app.llm import LLMError

    register(client, invite, "worker-failure-reply@test.dev")
    chat_id, bots = setup_workspace(client)
    researcher = next(b for b in bots if b["name"] == "研究员")
    supervisor_turns = 0

    async def fake_stream_chat(**kw):
        nonlocal supervisor_turns
        if kw["ctx"]["role"] == "worker":
            raise LLMError("模型接口返回 429: rate limit")
        supervisor_turns += 1
        if supervisor_turns == 1:
            yield {
                "type": "done", "content": "", "reasoning_content": "派活思考",
                "tool_calls": [{
                    "id": "delegate-failure", "type": "function",
                    "function": {"name": "delegate", "arguments": json.dumps({
                        "bot": researcher["name"], "instruction": "整理登录需求",
                    }, ensure_ascii=False)},
                }],
            }
            return
        assert any(m["role"] == "tool" and "429" in m["content"] for m in kw["messages"])
        yield {"type": "done", "content": "研究员暂时受限，下面由我给出登录需求", "reasoning_content": "处理失败", "tool_calls": []}

    monkeypatch.setattr(orchestrator, "stream_chat", fake_stream_chat)
    run_id = client.post(
        f"/api/chats/{chat_id}/messages", json={"content": "帮我设计登录需求"}
    ).json()["run"]["id"]
    run_worker(run_id)
    wait_status(client, run_id, {"completed"})
    assert supervisor_turns == 2
    assert any(m["content"] == "研究员暂时受限，下面由我给出登录需求" for m in messages(client, chat_id))


def test_plan_resume_keeps_tool_history_valid(client, invite):
    """计划恢复必须回答 propose_plan 的 tool_call，且历史里不能有悬空调用。"""
    from sqlalchemy import select

    from app.db import SessionLocal
    from app.models import Thread
    from app.orchestrator import add_message

    register(client, invite, "toolhist@test.dev")
    chat_id, _ = setup_workspace(client)

    run_id = client.post(
        f"/api/chats/{chat_id}/messages",
        json={"content": "调研 3 家国内向量数据库厂商，写一份对比报告。"},
    ).json()["run"]["id"]
    run_worker(run_id)
    wait_status(client, run_id, {"waiting"})

    call = next(m for m in messages(client, chat_id) if m["type"] == "tool_call")
    call_id = call["meta"]["tool_calls"][0]["id"]
    assert call["meta"]["tool_calls"][0]["function"]["name"] == "propose_plan"

    # 暂停期间历史不完整，构造上下文时必须丢掉悬空的 propose_plan，而不是把错误结构发给模型
    paused = build_main_history(run_id, chat_id)
    assert not [
        m for m in paused if m.get("role") == "assistant" and m.get("tool_calls")
    ], "暂停期间悬空的 propose_plan 不应进入模型上下文"

    # 模拟暂停期间群里插入了系统消息（例如成员变动），它不能插在 tool_call 与 tool_result 之间
    with SessionLocal() as db:
        thread = db.scalar(
            select(Thread).where(Thread.chat_id == chat_id, Thread.type == "main")
        )
        add_message(
            db,
            thread=thread,
            run_id=run_id,
            sender_type="system",
            type_="system",
            content="已加入成员：测试成员",
        )
        db.commit()

    assert client.post(f"/api/runs/{run_id}/plan/approve", json={}).status_code == 200
    run = client.get(f"/api/runs/{run_id}").json()
    assert run["resume"]["tool_call"]["id"] == call_id, "批准计划后必须保留 propose_plan 的 tool_call"

    run_worker(run_id)

    msgs = messages(client, chat_id)
    answered = [
        m
        for m in msgs
        if m["type"] == "tool_result" and (m["meta"] or {}).get("tool_call_id") == call_id
    ]
    assert answered, "恢复后必须给 propose_plan 补一条 tool_result"

    assert_tool_history_valid(build_main_history(run_id, chat_id))


def test_approved_plan_returns_edited_assignments_to_supervisor(client, invite, monkeypatch):
    """计划被用户修改后，恢复调用必须读到最终分工而不是原计划。"""
    import json

    from app import orchestrator

    register(client, invite, "edited-plan-delegation@test.dev")
    chat_id, _ = setup_workspace(client)
    run_id = client.post(
        f"/api/chats/{chat_id}/messages", json={"content": "整理资料并写报告"}
    ).json()["run"]["id"]
    run_worker(run_id)
    wait_status(client, run_id, {"waiting"})
    edited = [{"title": "只整理需求，不要联网检索", "bot": "写手", "expected": "一页需求文档"}]
    assert client.post(f"/api/runs/{run_id}/plan/approve", json={"steps": edited}).status_code == 200
    supervisor_turns = 0

    async def fake_stream_chat(**kw):
        nonlocal supervisor_turns
        if kw["ctx"]["role"] == "worker":
            assert kw["ctx"]["bot_name"] == "写手"
            assert kw["ctx"]["task"] == edited[0]["title"]
            yield {"type": "done", "content": "需求整理完成", "tool_calls": []}
            return
        supervisor_turns += 1
        if supervisor_turns == 1:
            assert any(m["role"] == "tool" and json.dumps(edited, ensure_ascii=False) in m["content"] for m in kw["messages"])
            yield {"type": "done", "content": "", "tool_calls": [{
                "id": "edited-plan-delegate", "type": "function", "function": {
                    "name": "delegate", "arguments": json.dumps({"bot": "写手", "instruction": edited[0]["title"]}, ensure_ascii=False),
                },
            }]}
            return
        yield {"type": "done", "content": "已按修改后的计划完成", "tool_calls": []}

    monkeypatch.setattr(orchestrator, "stream_chat", fake_stream_chat)
    run_worker(run_id)
    wait_status(client, run_id, {"completed"})
    assert supervisor_turns == 2


def test_history_cursor_replays_only_events_after_snapshot(client, invite):
    """进入群聊从消息快照游标订阅，只接收后续事件，避免整段历史重放。"""
    from sqlalchemy import func, select

    from app.api.stream import _stream
    from app.db import SessionLocal
    from app.events import emit
    from app.models import Chat, Event

    register(client, invite, "history-cursor@test.dev")
    chat_id, _ = setup_workspace(client)
    client.post(f"/api/chats/{chat_id}/messages", json={"content": "历史消息"})
    snapshot = client.get(f"/api/chats/{chat_id}/messages").json()
    with SessionLocal() as db:
        user_id = db.get(Chat, chat_id).user_id
        latest = db.scalar(select(func.max(Event.id)).where(Event.chat_id == chat_id))
        assert snapshot["event_cursor"] == latest and latest > 0
        emit(db, user_id, chat_id, "message.delta", {"message_id": "new-stream", "content": "新内容"})
        db.commit()

    class ConnectedRequest:
        async def is_disconnected(self):
            return False

    async def next_event():
        stream = _stream(user_id, chat_id, snapshot["event_cursor"], ConnectedRequest())
        try:
            assert await asyncio.wait_for(anext(stream), timeout=1) == ": connected\n\n"
            return await asyncio.wait_for(anext(stream), timeout=3)
        finally:
            await stream.aclose()

    event = asyncio.run(next_event())
    assert "event: message.delta" in event and "new-stream" in event
    assert "历史消息" not in event


def test_step_mode_resume_keeps_tool_history_valid(client, invite):
    """逐步确认：每次「继续」都要把子任务结果补成 delegate 的 tool_result。"""
    register(client, invite, "stepmode@test.dev")
    chat_id, _ = setup_workspace(client)
    client.patch(f"/api/chats/{chat_id}", json={"run_mode": "step"})

    run_id = client.post(
        f"/api/chats/{chat_id}/messages",
        json={"content": "调研 3 家国内向量数据库厂商，写一份对比报告。"},
    ).json()["run"]["id"]

    run_worker(run_id)
    wait_status(client, run_id, {"waiting"})  # 计划确认
    assert client.post(f"/api/runs/{run_id}/plan/approve", json={}).status_code == 200

    for _ in range(6):
        run_worker(run_id)
        run = client.get(f"/api/runs/{run_id}").json()
        if run["status"] != "waiting":
            break
        assert run["resume"]["kind"] == "step", f"意外暂停：{run['resume']}"
        # 暂停时 delegate 还没有 tool_result，构造上下文必须丢掉它
        assert_tool_history_valid(build_main_history(run_id, chat_id))
        assert client.post(f"/api/runs/{run_id}/continue", json={}).status_code == 200

    assert run["status"] == "completed", run
    assert_tool_history_valid(build_main_history(run_id, chat_id))
    done_results = [
        m
        for m in messages(client, chat_id)
        if m["type"] == "tool_result" and "任务已完成" in m["content"]
    ]
    assert len(done_results) >= 2, "每个子任务完成都应有一条 delegate 的工具结果"


def test_at_bot_direct(client, invite):
    register(client, invite, "mention@test.dev")
    chat_id, bots = setup_workspace(client)
    researcher = next(b for b in bots if b["name"] == "研究员")

    run_id = client.post(
        f"/api/chats/{chat_id}/messages", json={"content": "@研究员 你好，简单说一句。"}
    ).json()["run"]["id"]
    run_worker(run_id)

    run = wait_status(client, run_id, {"completed"})
    assert run["target_bot_id"] == researcher["id"]
    types = [m["type"] for m in messages(client, chat_id)]
    assert "plan" not in types, "@Bot 直连不应经过总指挥拆解"


def test_sandbox_escape_rejected(client, invite):
    register(client, invite, "sandbox@test.dev")
    chat_id, _ = setup_workspace(client)

    from app import sandbox

    with pytest.raises(sandbox.SandboxError):
        sandbox.safe_path("u1", chat_id, "../../etc/passwd")

    with pytest.raises(sandbox.SandboxError):
        sandbox.safe_path("u1", chat_id, "/etc/passwd")


def test_dissolve_chat(client, invite):
    """解散群聊：执行中拒绝，停止后可删，且连带清理消息与子任务。"""
    register(client, invite, "dissolve@test.dev")
    chat_id, _ = setup_workspace(client)

    run_id = client.post(
        f"/api/chats/{chat_id}/messages", json={"content": "做个复杂任务。"}
    ).json()["run"]["id"]

    # 有进行中的执行时不允许解散
    assert client.delete(f"/api/chats/{chat_id}").status_code == 409

    client.post(f"/api/runs/{run_id}/stop")
    r = client.delete(f"/api/chats/{chat_id}")
    assert r.status_code == 200, r.text

    assert client.get(f"/api/chats/{chat_id}").status_code == 404
    assert all(c["id"] != chat_id for c in client.get("/api/chats").json())


def test_member_add_remove_and_supervisor_handover(client, invite):
    """CHT-01：可随时增减成员；总指挥必须先交接才能移出。"""
    register(client, invite, "members@test.dev")
    chat_id, bots = setup_workspace(client)
    supervisor = next(b for b in bots if b["role"] == "总指挥")
    member_names = {m["name"] for m in client.get(f"/api/chats/{chat_id}").json()["members"]}

    new_bot = client.post(
        "/api/bots",
        json={"name": "数据分析师", "avatar": "📊", "role": "数据分析师"},
    ).json()
    assert new_bot["name"] not in member_names

    # 加入成员：立刻出现在群聊成员里，并在群里留一条系统消息
    r = client.post(f"/api/chats/{chat_id}/members", json={"bot_ids": [new_bot["id"]]})
    assert r.status_code == 200, r.text
    assert new_bot["id"] in {m["id"] for m in r.json()["members"]}
    chat = client.get(f"/api/chats/{chat_id}").json()
    assert new_bot["id"] in {m["id"] for m in chat["members"]}
    notes = [m for m in messages(client, chat_id) if m["type"] == "system" and "已加入成员" in m["content"]]
    assert notes and "数据分析师" in notes[-1]["content"]

    # 重复加入是幂等的：不重复建成员，也不重复留消息
    count_before = len(chat["members"])
    assert client.post(
        f"/api/chats/{chat_id}/members", json={"bot_ids": [new_bot["id"]]}
    ).status_code == 200
    assert len(client.get(f"/api/chats/{chat_id}").json()["members"]) == count_before
    added_notes = [m for m in messages(client, chat_id) if m["type"] == "system" and "已加入成员" in m["content"]]
    assert len(added_notes) == len(notes)

    # 总指挥不能直接移出
    assert client.delete(f"/api/chats/{chat_id}/members/{supervisor['id']}").status_code == 409

    # 移出普通成员
    assert client.delete(f"/api/chats/{chat_id}/members/{new_bot['id']}").status_code == 200
    assert new_bot["id"] not in {m["id"] for m in client.get(f"/api/chats/{chat_id}").json()["members"]}

    # 不在群里的 Bot 不能当总指挥，也不能重复移出
    assert client.patch(
        f"/api/chats/{chat_id}", json={"supervisor_bot_id": new_bot["id"]}
    ).status_code == 400
    assert client.delete(f"/api/chats/{chat_id}/members/{new_bot['id']}").status_code == 404

    # 重新加入 → 交接总指挥 → 旧总指挥可以移出
    assert client.post(
        f"/api/chats/{chat_id}/members", json={"bot_ids": [new_bot["id"]]}
    ).status_code == 200
    r = client.patch(f"/api/chats/{chat_id}", json={"supervisor_bot_id": new_bot["id"]})
    assert r.status_code == 200, r.text
    assert r.json()["supervisor_bot_id"] == new_bot["id"]
    assert client.delete(f"/api/chats/{chat_id}/members/{supervisor['id']}").status_code == 200
    chat = client.get(f"/api/chats/{chat_id}").json()
    assert chat["supervisor_bot_id"] == new_bot["id"]
    assert supervisor["id"] not in {m["id"] for m in chat["members"]}
    assert any(m["type"] == "system" and "已将总指挥更换为" in m["content"] for m in messages(client, chat_id))

    # 越权与无效 Bot 一律拒绝
    assert client.post(
        f"/api/chats/{chat_id}/members", json={"bot_ids": ["not-a-bot"]}
    ).status_code == 400
    assert client.delete(f"/api/chats/{chat_id}/members/not-a-bot").status_code == 404



def test_langgraph_checkpoint_holds_the_pause(client, invite):
    """编排跑在 LangGraph 上：暂停必须作为 interrupt 留在检查点里，Worker 重启后能恢复。"""
    register(client, invite, "lgckpt@test.dev")
    chat_id, _ = setup_workspace(client)

    run_id = client.post(
        f"/api/chats/{chat_id}/messages",
        json={"content": "调研 3 家国内向量数据库厂商，写一份对比报告。"},
    ).json()["run"]["id"]
    run_worker(run_id)
    run = wait_status(client, run_id, {"waiting"})
    assert run["resume"]["kind"] == "plan"

    from app.graph import build_graph, open_checkpointer, run_config
    from app.orchestrator import RunExecutor

    async def read_pending():
        async with open_checkpointer() as saver:
            app = build_graph(RunExecutor(run_id), saver)
            snap = await app.aget_state(run_config(run_id))
            return [i.value for i in snap.interrupts], dict(snap.values)

    pending, values = asyncio.run(read_pending())
    assert pending, "暂停应写进 LangGraph 检查点"
    assert pending[0]["kind"] == "plan"
    assert pending[0]["plan"], "计划步骤要跟着 interrupt 一起存下来"
    assert values.get("role") == "supervisor"
    assert values.get("thread_id")


def test_interjection_joins_the_running_execution(client, invite):
    """RUN-13：执行中补充不新建执行，总指挥在下一轮模型调用前读到。"""
    from sqlalchemy import func, select

    from app.db import SessionLocal
    from app.models import Run

    register(client, invite, "interject@test.dev")
    chat_id, _ = setup_workspace(client)
    run_id = client.post(
        f"/api/chats/{chat_id}/messages",
        json={"content": "调研 3 家国内向量数据库厂商，写一份对比报告。"},
    ).json()["run"]["id"]
    run_worker(run_id)
    wait_status(client, run_id, {"waiting"})

    r = client.post(
        f"/api/chats/{chat_id}/messages",
        json={"content": "补充：只看国内厂商，重点比较价格。", "interject": True},
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["interjection"] is True
    assert body["run"]["id"] == run_id
    assert body["message"]["meta"]["kind"] == "interjection"

    # 普通消息仍然不允许并发，且不能因为插话多出一条执行
    assert client.post(
        f"/api/chats/{chat_id}/messages", json={"content": "另一条普通消息"}
    ).status_code == 409
    with SessionLocal() as db:
        count = db.scalar(select(func.count(Run.id)).where(Run.chat_id == chat_id))
    assert count == 1

    built = build_main_history(run_id, chat_id)
    user_msgs = [m["content"] for m in built if m["role"] == "user"]
    assert "补充：只看国内厂商，重点比较价格。" in user_msgs, "插话正文原样进入上下文"
    assert any(
        m["role"] == "system" and "执行过程中补充" in m["content"] for m in built
    ), "插话要用系统提示标注优先级，且不污染用户原话"

    # 恢复后仍然能继续执行完
    assert client.post(f"/api/runs/{run_id}/plan/approve", json={}).status_code == 200
    run_worker(run_id)
    wait_status(client, run_id, {"completed", "waiting", "failed"})


def test_always_allow_tool_for_this_chat(client, invite):
    """APV-06：勾选「本群聊始终允许」后，同类工具不再弹审批。"""
    register(client, invite, "alwaysallow@test.dev")
    chat_id, _ = setup_workspace(client)
    client.patch(f"/api/chats/{chat_id}", json={"require_approval_for_write": True})

    run_id = client.post(
        f"/api/chats/{chat_id}/messages", json={"content": "整理一份资料并写成文件。"}
    ).json()["run"]["id"]
    run_worker(run_id)
    wait_status(client, run_id, {"waiting"})
    client.post(f"/api/runs/{run_id}/plan/approve", json={})

    run_worker(run_id)
    run = wait_status(client, run_id, {"waiting"})
    assert run["resume"]["kind"] == "approval"

    pending = [a for a in client.get("/api/approvals").json() if a["status"] == "pending"]
    assert pending and pending[0]["tool"] == "file_write" and pending[0]["level"] == "L2"

    r = client.post(
        f"/api/approvals/{pending[0]['id']}/approve", json={"always_allow": True}
    )
    assert r.status_code == 200, r.text

    chat = client.get(f"/api/chats/{chat_id}").json()
    assert "file_write" in chat["always_allow_tools"]

    # 继续跑：白名单生效，不应再出现 file_write 的待审批
    run_worker(run_id)
    wait_status(client, run_id, {"completed", "waiting", "failed"})
    still = [
        a
        for a in client.get("/api/approvals").json()
        if a["status"] == "pending" and a["tool"] == "file_write"
    ]
    assert not still


def test_usage_summary_and_cost(client, invite):
    """LIM-05 / MOD-05：用量按 Bot、模型、日期聚合，填了单价后能算出费用。"""
    register(client, invite, "usage@test.dev")
    chat_id, _ = setup_workspace(client)
    run_id = client.post(
        f"/api/chats/{chat_id}/messages",
        json={"content": "调研 3 家国内向量数据库厂商，写一份对比报告。"},
    ).json()["run"]["id"]
    run_worker(run_id)
    wait_status(client, run_id, {"waiting"})
    client.post(f"/api/runs/{run_id}/plan/approve", json={})
    run_worker(run_id)
    wait_status(client, run_id, {"completed"})

    summary = client.get("/api/usage/summary?days=30").json()
    assert summary["totals"]["tokens"] > 0
    assert summary["totals"]["calls"] > 0
    assert summary["totals"]["runs"] >= 1
    assert summary["by_bot"] and summary["by_bot"][0]["tokens"] > 0
    assert summary["by_model"] and summary["by_model"][0]["model"] == "botly-mock"
    assert len(summary["daily"]) == 30
    assert summary["recent_runs"][0]["run_id"] == run_id
    assert summary["totals"]["cost"] == 0  # mock 单价为 0

    # 填入单价后，同一批历史调用立刻能算出估算费用
    p = client.get("/api/providers").json()[0]
    client.patch(
        f"/api/providers/{p['id']}",
        json={
            "models": [
                {"name": m["name"], "input_price": 1.0, "output_price": 2.0}
                for m in p["models"]
            ]
        },
    )
    priced = client.get("/api/usage/summary?days=30").json()
    assert priced["totals"]["cost"] > 0
    assert priced["totals"]["unpriced_tokens"] == 0
    assert priced["recent_runs"][0]["cost"] > 0


def test_long_delegation_loop_and_historical_task_status(client, invite, monkeypatch):
    """超过 25 个图节点仍能完成；下一轮不应丢掉旧任务状态和详情。"""
    import json
    from app import orchestrator

    register(client, invite, "long-loop@test.dev")
    chat_id, bots = setup_workspace(client)
    worker = next(b for b in bots if b["name"] == "研究员")
    completed = []
    supervisor_turns = 0

    async def fake_stream_chat(**kw):
        nonlocal supervisor_turns
        if kw["ctx"]["role"] == "worker":
            completed.append(kw["ctx"]["task"])
            yield {"type": "done", "content": f"完成 {kw['ctx']['task']}", "tool_calls": []}
            return
        supervisor_turns += 1
        if supervisor_turns == 1:
            yield {"type": "done", "content": "", "tool_calls": [{
                "id": f"long-delegate-{i}", "type": "function", "function": {
                    "name": "delegate", "arguments": json.dumps({"bot": worker["name"], "instruction": f"检查第 {i} 项"}),
                },
            } for i in range(9)]}
        else:
            assert len(completed) == 9
            assert_tool_history_valid(kw["messages"])
            yield {"type": "done", "content": "全部完成", "tool_calls": []}

    monkeypatch.setattr(orchestrator, "stream_chat", fake_stream_chat)
    run_id = client.post(f"/api/chats/{chat_id}/messages", json={"content": "检查九项需求"}).json()["run"]["id"]
    run_worker(run_id)
    wait_status(client, run_id, {"completed"})
    tasks = client.get(f"/api/chats/{chat_id}").json()["tasks"]
    assert len(tasks) == 9 and all(t["status"] == "completed" for t in tasks)
    old_task = tasks[0]
    client.post(f"/api/chats/{chat_id}/messages", json={"content": "下一轮"})
    notes = [m for m in messages(client, chat_id) if m["meta"].get("delegation")]
    assert len(notes) == 9 and all(m["meta"]["task_status"] == "completed" for m in notes)
    assert client.get(f"/api/tasks/{old_task['id']}").json()["status"] == "completed"
    history = client.get(f"/api/chats/{chat_id}/messages", params={"thread_id": old_task["thread_id"]}).json()
    assert any(m["content"] == "完成 检查第 0 项" for m in history["messages"])
    register(client, invite, "task-isolation@test.dev")
    assert client.get(f"/api/tasks/{old_task['id']}").status_code == 404


@pytest.mark.parametrize("failure", ["limit", "exception"])
def test_aborted_worker_settles_task_and_stream(client, invite, monkeypatch, failure):
    import json
    from app import orchestrator

    register(client, invite, f"aborted-worker-{failure}@test.dev")
    chat_id, bots = setup_workspace(client)
    worker = next(b for b in bots if b["name"] == "研究员")
    if failure == "limit":
        client.patch(f"/api/chats/{chat_id}", json={"limits_override": {"token_budget": 1000}})

    async def fake_stream_chat(**kw):
        if kw["ctx"]["role"] == "supervisor":
            yield {"type": "done", "content": "", "tool_calls": [{
                "id": "abort-delegate", "type": "function", "function": {"name": "delegate", "arguments": json.dumps({"bot": worker["name"], "instruction": "检查任务"})},
            }]}
        else:
            yield {"type": "delta", "text": "正在检查"}
            if failure == "exception":
                raise RuntimeError("模拟执行中断")
            yield {"type": "done", "content": "", "tool_calls": [{
                "id": "abort-tool", "type": "function", "function": {"name": "file_list", "arguments": "{}"},
            }], "usage": {"output_tokens": 1001}}

    monkeypatch.setattr(orchestrator, "stream_chat", fake_stream_chat)
    run_id = client.post(f"/api/chats/{chat_id}/messages", json={"content": "开始检查"}).json()["run"]["id"]
    run_worker(run_id)
    wait_status(client, run_id, {"failed"})
    chat = client.get(f"/api/chats/{chat_id}").json()
    assert chat["tasks"] and all(t["status"] == "failed" for t in chat["tasks"])
    assert all(b["status"] == "idle" for b in chat["members"])
    history = client.get(f"/api/chats/{chat_id}/messages", params={"thread_id": chat["tasks"][0]["thread_id"]}).json()
    assert all(m["status"] != "streaming" for m in history["messages"])


def test_completed_checkpoint_reconciles_run_without_replaying(client, invite, monkeypatch):
    """图已结束、业务状态被租约恢复时，只补收尾，不重跑模型。"""
    from app import orchestrator
    from app.db import SessionLocal
    from app.models import Run

    register(client, invite, "finished-checkpoint@test.dev")
    chat_id, _ = setup_workspace(client)

    async def finish(**kw):
        yield {"type": "done", "content": "已完成", "tool_calls": []}

    monkeypatch.setattr(orchestrator, "stream_chat", finish)
    run_id = client.post(f"/api/chats/{chat_id}/messages", json={"content": "回复一句"}).json()["run"]["id"]
    run_worker(run_id)
    wait_status(client, run_id, {"completed"})
    count = len(messages(client, chat_id))
    with SessionLocal() as db:
        db.get(Run, run_id).status = "queued"
        db.commit()
    run_worker(run_id)
    wait_status(client, run_id, {"completed"})
    assert len(messages(client, chat_id)) == count


def test_event_reconnect_uses_latest_cursor():
    from app.api.stream import _initial_id
    assert _initial_id("120", 100) == 120
    assert _initial_id("80", 100) == 100
    assert _initial_id("invalid", 100) == 100


def test_model_timeout_has_a_visible_reason(monkeypatch):
    import httpx
    from app import llm

    async def timed_out(**kw):
        raise httpx.ReadTimeout("")
        yield

    monkeypatch.setattr(llm, "_stream_openai", timed_out)
    monkeypatch.setattr(llm, "LLM_MAX_RETRIES", 0)

    async def invoke():
        async for _ in llm.stream_chat(kind="openai", base_url="http://test/v1", api_key="", model="test", messages=[]):
            pass
    with pytest.raises(llm.LLMError, match="请求超时"):
        asyncio.run(invoke())


def test_approval_resume_counts_each_tool_once(client, invite, monkeypatch):
    """审批前后重放工具节点，不能把同一次调用算成多步。"""
    import json
    from app import orchestrator

    register(client, invite, "resume-step-count@test.dev")
    chat_id, _ = setup_workspace(client)
    client.patch(f"/api/chats/{chat_id}", json={"require_approval_for_write": True})
    calls = 0

    async def fake(**kw):
        nonlocal calls
        calls += 1
        if calls == 1:
            yield {"type": "done", "content": "", "tool_calls": [{"id": "write-once", "type": "function", "function": {
                "name": "file_write", "arguments": json.dumps({"path": "once.txt", "content": "只写一次"}),
            }}]}
        else:
            yield {"type": "done", "content": "写入完成", "tool_calls": []}

    monkeypatch.setattr(orchestrator, "stream_chat", fake)
    run_id = client.post(f"/api/chats/{chat_id}/messages", json={"content": "写文件"}).json()["run"]["id"]
    run_worker(run_id)
    paused = wait_status(client, run_id, {"waiting"})
    assert paused["steps_used"] == 2
    ap = client.get("/api/approvals").json()[0]
    assert client.post(f"/api/approvals/{ap['id']}/approve", json={}).status_code == 200
    run_worker(run_id)
    finished = wait_status(client, run_id, {"completed"})
    assert finished["steps_used"] == 3 and calls == 2


def test_batch_tool_calls_ignore_old_step_budget(client, invite, monkeypatch):
    from app import orchestrator
    register(client, invite, "batch-step-count@test.dev")
    chat_id, _ = setup_workspace(client)
    client.patch(f"/api/chats/{chat_id}", json={"limits_override": {"max_steps": 3}})
    turns = 0

    async def fake(**kw):
        nonlocal turns
        turns += 1
        if turns == 1:
            yield {"type": "done", "content": "", "tool_calls": [{"id": f"list-{i}", "type": "function", "function": {
                "name": "file_list", "arguments": "{}",
            }} for i in range(8)]}
        else:
            yield {"type": "done", "content": "全部完成", "tool_calls": []}
    monkeypatch.setattr(orchestrator, "stream_chat", fake)
    run_id = client.post(f"/api/chats/{chat_id}/messages", json={"content": "检查文件"}).json()["run"]["id"]
    run_worker(run_id)
    run = wait_status(client, run_id, {"completed"})
    assert run["steps_used"] == 10
    assert len([m for m in messages(client, chat_id) if m["type"] == "tool_result"]) == 8
