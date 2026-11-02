"""思考模式（DeepSeek 等）的历史回传：assistant 消息必须带 reasoning_content。

线上问题：子任务失败于
`400 The reasoning_content in the thinking mode must be passed back to the API`。
根因是崩溃/重启后残留的流式草稿被标成 `interrupted`，构造上下文时它作为
assistant 消息进入了请求，却没有 `reasoning_content` 字段。
"""
from __future__ import annotations

from sqlalchemy import select

from app.db import SessionLocal
from app.models import Bot, Chat, Message, Run, Task, Thread, User
from app.orchestrator import RunExecutor
from tests.conftest import register, setup_workspace


def _setup(client, invite, email):
    register(client, invite, email)
    chat_id, bots = setup_workspace(client)
    run_id = client.post(f"/api/chats/{chat_id}/messages", json={"content": "整理资料"}).json()["run"]["id"]
    worker = next(b for b in bots if b["name"] == "研究员")
    with SessionLocal() as db:
        run = db.get(Run, run_id)
        main = db.scalar(select(Thread).where(Thread.chat_id == chat_id, Thread.type == "main"))
        thread = Thread(
            user_id=run.user_id,
            chat_id=chat_id,
            parent_thread_id=main.id,
            type="task",
            title="资料收集",
            bot_id=worker["id"],
        )
        db.add(thread)
        db.flush()
        task = Task(
            user_id=run.user_id,
            run_id=run_id,
            thread_id=thread.id,
            assigned_bot_id=worker["id"],
            instruction="资料收集",
            status="running",
        )
        db.add(task)
        db.commit()
        return run_id, thread.id, worker["id"]


def _add(db, run, thread_id, bot_id, **kwargs) -> Message:
    kwargs.setdefault("sender_type", "bot")
    msg = Message(
        user_id=run.user_id,
        thread_id=thread_id,
        run_id=run.id,
        sender_bot_id=bot_id,
        **kwargs,
    )
    db.add(msg)
    db.flush()
    return msg


def _build(run_id: str, thread_id: str, bot_id: str) -> list[dict]:
    with SessionLocal() as db:
        run = db.get(Run, run_id)
        chat = db.get(Chat, run.chat_id)
        thread = db.get(Thread, thread_id)
        bot = db.get(Bot, bot_id)
        members = list(db.scalars(select(Bot).where(Bot.user_id == run.user_id)).all())
        return RunExecutor(run_id)._build_messages(
            db, run, chat, thread, bot, is_supervisor=False, members=members
        )


def test_interrupted_draft_is_not_sent_back(client, invite):
    run_id, thread_id, bot_id = _setup(client, invite, "reason-interrupt@test.dev")
    with SessionLocal() as db:
        run = db.get(Run, run_id)
        call = {
            "id": "call-r1",
            "type": "function",
            "function": {"name": "file_list", "arguments": "{}"},
        }
        _add(db, run, thread_id, bot_id, seq=2, type="tool_call", content="我先看看沙箱里有什么",
             reasoning_content="先列文件", meta={"tool_calls": [call]})
        _add(db, run, thread_id, None, seq=3, type="tool_result", sender_type="system",
             content="- report.md（100 字节）", meta={"tool_call_id": "call-r1", "tool": "file_list"})
        # 崩溃残留的草稿：被标成 interrupted 且没有 reasoning_content
        _add(db, run, thread_id, bot_id, seq=4, type="text",
             content="字节数仍偏高，我再压缩", status="interrupted", reasoning_content=None)
        db.commit()

    messages = _build(run_id, thread_id, bot_id)

    # 所有 assistant 消息都必须带 reasoning_content（空字符串也要显式给）
    for item in messages:
        if item["role"] == "assistant":
            assert "reasoning_content" in item, item
    # 半句草稿不进上下文，否则既误导模型又缺思维链
    assert not any("字节数仍偏高" in (i.get("content") or "") for i in messages)
    # 正常的工具轮仍然原样带回思维链
    tool_items = [i for i in messages if i["role"] == "assistant" and i.get("tool_calls")]
    assert tool_items and tool_items[0]["reasoning_content"] == "先列文件"


def test_assistant_text_without_reasoning_gets_empty_field(client, invite):
    """历史里没有思维链的 assistant 文本（老数据）也要补空字段，不能让请求缺 key。"""
    run_id, thread_id, bot_id = _setup(client, invite, "reason-empty@test.dev")
    with SessionLocal() as db:
        run = db.get(Run, run_id)
        _add(db, run, thread_id, bot_id, seq=2, type="text",
             content="这是上一轮的最终回答", status="done", reasoning_content=None)
        db.commit()

    messages = _build(run_id, thread_id, bot_id)
    texts = [
        i for i in messages
        if i["role"] == "assistant" and (i.get("content") or "").startswith("这是上一轮")
    ]
    assert texts, messages
    assert texts[0]["reasoning_content"] == ""
