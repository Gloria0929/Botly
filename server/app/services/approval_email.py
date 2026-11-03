"""审批邮件投递：数据库持久队列、租约认领、失败重试。"""
from __future__ import annotations

from datetime import timedelta
from html import escape
from urllib.parse import urlencode

from sqlalchemy import or_, select, update

from app.core.config import MAIL_DEV, PUBLIC_BASE_URL
from app.core.db import session_scope
from app.core.models import Approval, Notification, User, UserSettings, now
from app.services import mail

MAX_ATTEMPTS = 5


def deliver_pending(limit: int = 20) -> int:
    """SMTP 在事务外执行；租约防止多个 Worker 同时投递同一封邮件。"""
    stamp = now()
    with session_scope() as db:
        ids = list(db.scalars(select(Notification.id).where(
            or_(Notification.email_status == "pending",
                (Notification.email_status == "sending") & (Notification.email_lease_until < stamp)),
            or_(Notification.email_next_attempt_at.is_(None), Notification.email_next_attempt_at <= stamp),
        ).order_by(Notification.created_at).limit(limit)))
    delivered = 0
    for notification_id in ids:
        with session_scope() as db:
            claimed = db.execute(update(Notification).where(
                Notification.id == notification_id,
                or_(Notification.email_status == "pending",
                    (Notification.email_status == "sending") & (Notification.email_lease_until < now())),
            ).values(email_status="sending", email_lease_until=now() + timedelta(minutes=2)))
            if not claimed.rowcount:
                continue
            n = db.get(Notification, notification_id)
            user = db.get(User, n.user_id)
            settings = db.get(UserSettings, n.user_id)
            approval = db.get(Approval, n.target_id) if n.target_type == "approval" else None
            if (not user or user.status != "active"
                or (settings and settings.approval_email_enabled is False)
                or (n.type == "approval" and (not approval or approval.status != "pending"))):
                n.email_status = "skipped"
                n.email_lease_until = None
                continue
            base = PUBLIC_BASE_URL or ("http://localhost:3200" if MAIL_DEV else "")
            recipient, title, body = user.email, n.title, n.body
            query = urlencode({"chat": n.chat_id or "", "target": "approval", "id": n.target_id or ""})
            link = f"{base}/chat?{query}"
            expires = approval.expires_at.isoformat() + " UTC" if approval and approval.expires_at else ""
        try:
            if not base:
                raise mail.MailNotConfigured("审批邮件需要配置 BOTLY_BASE_URL")
            text = f"{body}\n\n请登录 Botly 查看并处理审批：\n{link}\n\n"
            if expires:
                text += f"审批有效期至：{expires}\n"
            html = (f"<p>{escape(body)}</p><p><a href=\"{escape(link, quote=True)}\">查看审批</a></p>"
                    f"<p>请使用收到此邮件的账号登录。{escape(expires)}</p>")
            sent = mail.send(recipient, f"Botly · {title}", text, html)
        except Exception as exc:
            with session_scope() as db:
                n = db.get(Notification, notification_id)
                if not n:
                    continue
                n.email_attempts = (n.email_attempts or 0) + 1
                n.email_status = "failed" if n.email_attempts >= MAX_ATTEMPTS else "pending"
                n.email_next_attempt_at = now() + timedelta(seconds=min(3600, 60 * 2 ** (n.email_attempts - 1)))
                n.email_lease_until = None
                # 不把 SMTP 异常全文（可能包含凭据）写进业务表。
                n.email_last_error = type(exc).__name__
        else:
            with session_scope() as db:
                n = db.get(Notification, notification_id)
                if n:
                    n.email_status = "sent" if sent else "dev"
                    n.email_attempts = (n.email_attempts or 0) + 1
                    n.email_lease_until = None
                    n.email_last_error = None
            delivered += 1
    return delivered
