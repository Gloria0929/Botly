"""审批邮件必须提交后投递、只到所属账号、失败可重试。"""
from datetime import timedelta

from app.core.db import SessionLocal
from app.core.events import notify
from app.core.models import Notification, now
from app.services import approval_email, push
from tests.conftest import register
from tests.test_approval_state import approval_fixture


def test_approval_queues_mail_and_never_pushes(client, invite, monkeypatch):
    chat_id, rid, aid, _ = approval_fixture(client, invite, "email")
    user_id = client.get("/api/auth/me").json()["id"]
    pushes, sent = [], []
    monkeypatch.setattr(push, "push_notification", lambda *a, **kw: pushes.append(kw))
    monkeypatch.setattr(approval_email.mail, "send", lambda to, subject, text, html: sent.append((to, subject, text)) or True)
    with SessionLocal() as db:
        n = notify(db, user_id, "approval", "需要审批", "文件写入", chat_id=chat_id, target_type="approval", target_id=aid)
        notification_id = n.id
        assert pushes == []
        db.commit()
    register(client, invite, "approval-email-other@test.dev")
    sent.clear()  # 注册验证邮件与本测试的审批投递分开断言。
    assert approval_email.deliver_pending() >= 1
    assert sent == [("approval-state-email@test.dev", "Botly · 需要审批", sent[0][2])]
    assert f"id={aid}" in sent[0][2] and f"chat={chat_id}" in sent[0][2]
    with SessionLocal() as db:
        assert db.get(Notification, notification_id).email_status == "sent"
    approval_email.deliver_pending()
    assert len(sent) == 1


def test_rollback_does_not_send_mail(client, invite, monkeypatch):
    data = register(client, invite, "approval-email-rollback@test.dev")
    sent = []
    monkeypatch.setattr(approval_email.mail, "send", lambda *a, **kw: sent.append(a) or True)
    with SessionLocal() as db:
        n = notify(db, data["id"], "approval_expired", "已过期", chat_id=data["chat_id"])
        notification_id = n.id
        db.rollback()
    approval_email.deliver_pending()
    assert sent == []
    with SessionLocal() as db:
        assert db.get(Notification, notification_id) is None


def test_mail_failure_retries_and_user_switch_is_independent(client, invite, monkeypatch):
    data = register(client, invite, "approval-email-retry@test.dev")
    client.patch("/api/settings", json={"notifications_enabled": False})
    with SessionLocal() as db:
        n = notify(db, data["id"], "approval_expired", "已过期", chat_id=data["chat_id"])
        notification_id = n.id
        db.commit()
    def fail(*args):
        raise RuntimeError("SMTP 临时失败")
    monkeypatch.setattr(approval_email.mail, "send", fail)
    approval_email.deliver_pending()
    with SessionLocal() as db:
        n = db.get(Notification, notification_id)
        assert n.email_status == "pending" and n.email_attempts == 1
        assert n.email_last_error == "RuntimeError"
        n.email_next_attempt_at = now() - timedelta(seconds=1)
        db.commit()
    sent = []
    monkeypatch.setattr(approval_email.mail, "send", lambda *a: sent.append(a) or True)
    approval_email.deliver_pending()
    assert len(sent) == 1
    with SessionLocal() as db:
        assert db.get(Notification, notification_id).email_status == "sent"


def test_disabled_email_skips_and_clear_preserves_queue(client, invite, monkeypatch):
    data = register(client, invite, "approval-email-disabled@test.dev")
    client.patch("/api/settings", json={"approval_email_enabled": False})
    with SessionLocal() as db:
        n = notify(db, data["id"], "approval_expired", "已过期", chat_id=data["chat_id"])
        notification_id = n.id
        db.commit()
    client.delete("/api/notifications")
    assert client.get("/api/notifications").json()["items"] == []
    with SessionLocal() as db:
        assert db.get(Notification, notification_id).email_status == "pending"
    sent = []
    monkeypatch.setattr(approval_email.mail, "send", lambda *a: sent.append(a) or True)
    approval_email.deliver_pending()
    assert not sent
    with SessionLocal() as db:
        assert db.get(Notification, notification_id).email_status == "skipped"
