"""ACC-07 账号删除与 15.2 群聊导出的回归测试。"""
from __future__ import annotations

from datetime import timedelta

from sqlalchemy import select

from app.accounts import purge_due_accounts
from app.db import SessionLocal
from app.models import Chat, User, now
from scripts.purge_accounts import _restore
from tests.conftest import register, setup_workspace


def test_export_chat_markdown(client, invite):
    register(client, invite, "export-chat@test.dev")
    chat_id, _ = setup_workspace(client)
    content = "导出测试：把这句话写进记录。"
    assert client.post(f"/api/chats/{chat_id}/messages", json={"content": content}).status_code == 201

    r = client.get(f"/api/chats/{chat_id}/export")
    assert r.status_code == 200, r.text
    assert r.headers["content-type"].startswith("text/markdown")
    assert "attachment" in r.headers["content-disposition"]
    body = r.text
    assert content in body
    assert "## 20" in body  # 按日期分组
    assert "**用户**" in body
    assert "北京时间" in body


def test_export_other_users_chat_is_404(client, invite):
    register(client, invite, "export-owner@test.dev")
    chat_id, _ = setup_workspace(client)
    register(client, invite, "export-other@test.dev")
    assert client.get(f"/api/chats/{chat_id}/export").status_code == 404


def test_delete_account_requires_password_and_email(client, invite):
    email = "delete-confirm@test.dev"
    register(client, invite, email)

    assert client.request(
        "DELETE", "/api/auth/account", json={"password": "wrong-pass", "confirm_email": email}
    ).status_code == 400
    assert client.request(
        "DELETE", "/api/auth/account", json={"password": "password-123", "confirm_email": "x@test.dev"}
    ).status_code == 400
    assert client.get("/api/auth/me").status_code == 200


def test_delete_account_disables_login_and_purges(client, invite):
    email = "delete-purge@test.dev"
    register(client, invite, email)
    chat_id, _ = setup_workspace(client)
    client.post(f"/api/chats/{chat_id}/messages", json={"content": "删除前的一条消息"})

    r = client.request(
        "DELETE", "/api/auth/account", json={"password": "password-123", "confirm_email": email}
    )
    assert r.status_code == 200, r.text
    assert r.json()["retention_days"] == 30
    # 会话立即失效
    assert client.get("/api/auth/me").status_code == 401
    # 停用后不能重新登录
    assert client.post(
        "/api/auth/login", json={"email": email, "password": "password-123"}
    ).status_code == 403

    with SessionLocal() as db:
        user = db.scalar(select(User).where(User.email == email))
        assert user and user.status == "deleted"
        assert user.purge_after > now()
        user_id = user.id
        # 未完成的执行被终止（这条消息刚入队）
        # 到期后彻底清除
        user.purge_after = now() - timedelta(seconds=1)
        db.commit()
        assert email in purge_due_accounts(db)
        db.commit()
        assert db.scalar(select(User).where(User.email == email)) is None
        assert not db.scalars(select(Chat).where(Chat.user_id == user_id)).all()


def test_deleted_account_can_be_restored_within_retention(client, invite):
    email = "delete-restore@test.dev"
    register(client, invite, email)
    assert client.request(
        "DELETE", "/api/auth/account", json={"password": "password-123", "confirm_email": email}
    ).status_code == 200

    with SessionLocal() as db:
        assert _restore(db, email, force=False) == 0
        db.commit()
        assert db.scalar(select(User).where(User.email == email)).status == "active"

    assert client.post(
        "/api/auth/login", json={"email": email, "password": "password-123"}
    ).status_code == 200
