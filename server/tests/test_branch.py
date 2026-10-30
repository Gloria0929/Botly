"""CHT-07：从任意消息拉出分支线程，不打断主线程。"""
from __future__ import annotations

import asyncio

from app.db import SessionLocal
from app.models import Run
from app.worker import process_one
from tests.conftest import register, setup_workspace


def test_branch_create_chat_and_isolated_run(client, invite):
    register(client, invite, "branch@test.dev")
    chat_id, _ = setup_workspace(client)
    r = client.post(f"/api/chats/{chat_id}/messages", json={"content": "主线程第一条"})
    assert r.status_code == 201, r.text
    msg_id = r.json()["message"]["id"]
    # 先把主线程这次执行停掉，避免 active run 阻止分支发消息
    client.post(f"/api/runs/{r.json()['run']['id']}/stop")

    b = client.post(f"/api/chats/{chat_id}/branches", json={"message_id": msg_id})
    assert b.status_code == 201, b.text
    branch = b.json()
    assert branch["title"]
    assert branch["anchor_message_id"] == msg_id

    # 分支快照包含锚点及之前的文本消息
    msgs = client.get(
        f"/api/chats/{chat_id}/messages?thread_id={branch['id']}"
    ).json()["messages"]
    assert [m["content"] for m in msgs] == ["主线程第一条"]

    # 在分支里追问：新建一次执行，但落在分支线程
    r2 = client.post(
        f"/api/chats/{chat_id}/messages",
        json={"content": "分支里的追问", "thread_id": branch["id"]},
    )
    assert r2.status_code == 201, r2.text
    run_id = r2.json()["run"]["id"]
    with SessionLocal() as db:
        assert db.get(Run, run_id).thread_id == branch["id"]

    asyncio.run(process_one(run_id))
    assert client.get(f"/api/runs/{run_id}").json()["status"] == "completed"

    # 回复写在分支里；主线程不受影响
    bmsgs = client.get(
        f"/api/chats/{chat_id}/messages?thread_id={branch['id']}"
    ).json()["messages"]
    assert any(m["sender_type"] == "bot" for m in bmsgs)
    main_msgs = client.get(f"/api/chats/{chat_id}/messages").json()["messages"]
    assert not any("分支里的追问" in m["content"] for m in main_msgs)

    # 分支列表能看到它
    branches = client.get(f"/api/chats/{chat_id}/branches").json()
    assert [x["id"] for x in branches] == [branch["id"]]


def test_branch_rejects_other_users_message(client, invite):
    mine = register(client, invite, "branch-owner@test.dev")
    chat_id, _ = setup_workspace(client)
    r = client.post(f"/api/chats/{chat_id}/messages", json={"content": "私有消息"})
    msg_id = r.json()["message"]["id"]

    register(client, invite, "branch-other@test.dev")
    assert client.post(
        f"/api/chats/{chat_id}/branches", json={"message_id": msg_id}
    ).status_code == 404
