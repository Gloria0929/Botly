"""审批记录、持久化消息卡片和实时事件必须一致。"""
from datetime import timedelta

import pytest
from sqlalchemy import select

from app.db import SessionLocal
from app.models import Approval, Chat, Event, Message, Run, Thread, now
from app.orchestrator import RunExecutor, add_message
from app.security import args_hash
from tests.conftest import register, setup_workspace


def approval_fixture(client, invite, label):
    register(client, invite, f"approval-state-{label}@test.dev")
    chat_id, _ = setup_workspace(client)
    run_id = client.post(f"/api/chats/{chat_id}/messages", json={"content": "写入文件，需要审批"}).json()["run"]["id"]
    with SessionLocal() as db:
        run = db.get(Run, run_id)
        bot_id = db.get(Chat, chat_id).supervisor_bot_id
        thread = db.scalar(select(Thread).where(Thread.chat_id == chat_id, Thread.type == "main"))
        args = {"path": "test.txt", "content": "测试内容"}
        ap = Approval(user_id=run.user_id, run_id=run.id, bot_id=bot_id, tool="file_write", args=args,
                      args_hash=args_hash(args), level="L2", expires_at=now()+timedelta(hours=24))
        db.add(ap)
        db.flush()
        card = add_message(db, thread=thread, run_id=run.id, sender_type="bot", sender_bot_id=bot_id,
                           type_="approval", content="请求执行 file_write", meta={"approval_id": ap.id, "status": "pending", "tool": "file_write", "level": "L2"})
        run.status = "waiting"
        run.resume_payload = {"kind": "approval", "approval_id": ap.id, "thread_id": thread.id, "bot_id": bot_id}
        db.commit()
        return chat_id, run.id, ap.id, card.id


def card_snapshot(client, chat_id, card_id):
    return next(m for m in client.get(f"/api/chats/{chat_id}/messages?limit=200").json()["messages"] if m["id"] == card_id)


def test_stop_updates_approval_card_and_emits_resolution(client, invite):
    chat_id, rid, aid, mid = approval_fixture(client, invite, "stop")
    assert client.post(f"/api/runs/{rid}/stop").status_code == 200
    with SessionLocal() as db:
        assert db.get(Approval, aid).status == "invalidated"
        assert db.get(Message, mid).meta["status"] == "invalidated"
        assert db.get(Message, mid).meta["closed_reason"] == "用户停止"
        events = db.scalars(select(Event).where(Event.type == "approval.resolved", Event.chat_id == chat_id)).all()
        assert any(e.payload["approval_id"] == aid and e.payload["status"] == "invalidated" for e in events)
    card = card_snapshot(client, chat_id, mid)
    assert card["meta"]["status"] == "invalidated" and card["meta"]["run_status"] == "stopped"
    response = client.post(f"/api/approvals/{aid}/approve", json={"always_allow": True})
    assert response.status_code == 400 and "已失效" in response.json()["detail"]
    assert "file_write" not in client.get(f"/api/chats/{chat_id}").json()["always_allow_tools"]
    assert client.get(f"/api/runs/{rid}").json()["status"] == "stopped"


@pytest.mark.parametrize("finish", ["failed", "limited", "completed"])
def test_terminal_paths_invalidate_pending_approval(client, invite, finish):
    chat_id, rid, aid, mid = approval_fixture(client, invite, finish)
    ex = RunExecutor(rid)
    if finish == "failed":
        ex._finish_failed("模型调用失败")
    elif finish == "limited":
        ex._finish_limited("步数超限")
    else:
        ex._finish_completed("执行结束")
    with SessionLocal() as db:
        assert db.get(Approval, aid).status == "invalidated"
        assert db.get(Message, mid).meta["status"] == "invalidated"
    assert card_snapshot(client, chat_id, mid)["meta"]["status"] == "invalidated"


@pytest.mark.parametrize("record_status", ["invalidated", "approved"])
def test_legacy_card_uses_canonical_approval_status(client, invite, record_status):
    chat_id, rid, aid, mid = approval_fixture(client, invite, f"legacy-{record_status}")
    with SessionLocal() as db:
        db.get(Run, rid).status = "stopped"
        db.get(Run, rid).end_reason = "用户停止"
        db.get(Approval, aid).status = record_status
        assert db.get(Message, mid).meta["status"] == "pending"
        db.commit()
    card = card_snapshot(client, chat_id, mid)
    assert card["meta"]["status"] == record_status
    if record_status == "invalidated":
        assert card["meta"]["closed_reason"] == "用户停止"


def test_current_approval_still_approves_with_allow_setting(client, invite):
    chat_id, rid, aid, mid = approval_fixture(client, invite, "valid")
    response = client.post(f"/api/approvals/{aid}/approve", json={"always_allow": True})
    assert response.status_code == 200 and response.json()["status"] == "queued"
    assert card_snapshot(client, chat_id, mid)["meta"]["status"] == "approved"
    assert "file_write" in client.get(f"/api/chats/{chat_id}").json()["always_allow_tools"]
    with SessionLocal() as db:
        assert db.get(Approval, aid).status == "approved"
        assert db.get(Message, mid).meta["status"] == "approved"
