"""P0 补齐项：TOL-01 搜索工具可用性、APV-03 审批过期交回总指挥。"""
from __future__ import annotations

from datetime import timedelta

from sqlalchemy import select

from app.db import SessionLocal
from app.maintenance import expire_overdue
from app.models import Approval, Chat, Message, Notification, Run, Thread, now
from app.orchestrator import add_message
from app.security import args_hash
from tests.conftest import register, setup_workspace


def _tools(client) -> dict[str, dict]:
    r = client.get("/api/bots/tools")
    assert r.status_code == 200, r.text
    return {t["name"]: t for t in r.json()}


def test_search_tool_requires_key(client, invite):
    """TOL-01：没配搜索 Key 时 web_search 不可选，配上之后恢复可选。"""
    register(client, invite, "tol01@test.dev")
    tools = _tools(client)
    assert tools["web_search"]["available"] is False
    assert "API Key" in (tools["web_search"]["reason"] or "")
    # 其它工具不受影响
    assert tools["web_read"]["available"] is True

    r = client.patch("/api/settings", json={"search_api_key": "tvly-test-key"})
    assert r.status_code == 200, r.text
    assert _tools(client)["web_search"]["available"] is True

    # 清空 Key 后再次不可用
    client.patch("/api/settings", json={"search_api_key": ""})
    assert _tools(client)["web_search"]["available"] is False


def _waiting_approval(client, invite, label: str, *, expired: bool = True, on_payload: bool = True):
    """造一条停在审批上的执行，可选让审批过期 / 让 resume_payload 指向别的审批。"""
    register(client, invite, f"apv03-{label}@test.dev")
    chat_id, bots = setup_workspace(client)
    run_id = client.post(
        f"/api/chats/{chat_id}/messages", json={"content": "写入文件，需要审批"}
    ).json()["run"]["id"]
    with SessionLocal() as db:
        run = db.get(Run, run_id)
        bot_id = db.get(Chat, chat_id).supervisor_bot_id
        thread = db.scalar(select(Thread).where(Thread.chat_id == chat_id, Thread.type == "main"))
        args = {"path": f"{label}.txt", "content": "x"}
        delta = timedelta(minutes=-5) if expired else timedelta(hours=24)
        ap = Approval(
            user_id=run.user_id,
            run_id=run.id,
            bot_id=bot_id,
            tool="file_write",
            args=args,
            args_hash=args_hash(args),
            level="L2",
            expires_at=now() + delta,
        )
        db.add(ap)
        db.flush()
        add_message(
            db,
            thread=thread,
            run_id=run.id,
            sender_type="bot",
            sender_bot_id=bot_id,
            type_="approval",
            content="请求执行 file_write",
            meta={"approval_id": ap.id, "status": "pending", "tool": "file_write"},
        )
        run.status = "waiting"
        run.resume_payload = {
            "kind": "approval",
            # 指向另一张审批：模拟「执行根本没停在这张审批上」
            "approval_id": ap.id if on_payload else "ap_other",
            "thread_id": thread.id,
            "bot_id": bot_id,
        }
        db.commit()
        return chat_id, run_id, ap.id


def test_approval_expiry_hands_back_to_supervisor(client, invite):
    """APV-03：审批过期不再直接判失败，而是带着原因回到总指挥。"""
    chat_id, run_id, ap_id = _waiting_approval(client, invite, "expired")

    with SessionLocal() as db:
        assert expire_overdue(db) >= 1
        db.commit()

        ap = db.get(Approval, ap_id)
        assert ap.status == "expired"
        run = db.get(Run, run_id)
        assert run.status == "queued", "过期后应重新入队，交给总指挥决定后续"
        assert (run.resume_payload or {}).get("approval_id") == ap_id

        sys_msgs = db.scalars(
            select(Message).where(
                Message.run_id == run_id,
                Message.type == "system",
                Message.content.like("审批已过期%"),
            )
        ).all()
        assert sys_msgs, "群聊里要说明审批过期并且已交回总指挥"
        assert "用户未在规定时间内处理" in sys_msgs[0].content

        notes = db.scalars(
            select(Notification).where(
                Notification.user_id == run.user_id,
                Notification.type == "approval_expired",
            )
        ).all()
        assert notes and "交回总指挥" in notes[0].body

    # 卡片状态同步为 expired，前端不再显示可点的批准/拒绝
    cards = client.get(f"/api/chats/{chat_id}/messages?limit=200").json()["messages"]
    card = next(m for m in cards if m["type"] == "approval" and m["meta"].get("approval_id") == ap_id)
    assert card["meta"]["status"] == "expired"


def test_approval_expiry_does_not_touch_other_waiting_run(client, invite):
    """过期的是另一张审批时，执行保持 waiting，不要被误唤醒。"""
    _, run_id, ap_id = _waiting_approval(client, invite, "unrelated", on_payload=False)
    with SessionLocal() as db:
        expire_overdue(db)
        db.commit()
        assert db.get(Approval, ap_id).status == "expired"
        assert db.get(Run, run_id).status == "waiting"
