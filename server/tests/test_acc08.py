"""ACC-08：找回密码与邮箱验证。

覆盖点：
- 申请重置 → 用邮件里的令牌改密 → 新密码可登录、旧密码失效；
- 旧会话（重置前签发的 Cookie）立即失效；
- 令牌一次性、过期即不可用；
- 不存在的邮箱也返回 200（防枚举），且不会产生可用令牌；
- 同类邮件 60 秒内重复申请被限流；
- 邮箱验证流程与 `me.email_verified` 一致。
"""
from __future__ import annotations

from datetime import timedelta

from app.db import SessionLocal
from app.models import EmailToken, User, now
from app.security import random_token, token_hash

from .conftest import login, register

MAIL_SUFFIX = "@test.dev"


def _new_user(client, invite, tag: str) -> str:
    email = f"acc08-{tag}{MAIL_SUFFIX}"
    register(client, invite, email)
    return email


def _token_from_mail(dev_link: str) -> str:
    assert "token=" in dev_link, dev_link
    return dev_link.split("token=", 1)[1]


def _logout(client) -> None:
    client.post("/api/auth/logout")


def test_forgot_password_returns_dev_link_on_plain_email(client, invite):
    email = _new_user(client, invite, "link")
    _logout(client)
    r = client.post("/api/auth/forgot-password", json={"email": email})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is True
    # 测试环境没配 SMTP：走开发模式，直接给可点链接
    assert body.get("dev_link", "").startswith("http")
    assert "/reset-password?token=" in body["dev_link"]


def test_reset_password_flow_invalidates_old_password_and_sessions(client, invite):
    email = _new_user(client, invite, "flow")
    # 先把当前会话换成“重置前”的一张，后面验证它会被踢掉
    login(client, email)
    old_cookie = client.cookies.get("botly_session")
    assert old_cookie
    _logout(client)

    r = client.post("/api/auth/forgot-password", json={"email": email})
    token = _token_from_mail(r.json()["dev_link"])

    # 重置前先用旧密码登录一次，确认基线可用
    login(client, email)

    r = client.post("/api/auth/reset-password", json={"token": token, "password": "new-password-456"})
    assert r.status_code == 200, r.text
    assert r.json()["email"] == email

    # 旧会话立刻失效
    client.cookies.set("botly_session", old_cookie)
    me = client.get("/api/auth/me")
    assert me.status_code == 401, me.text

    # 旧密码不能再登录，新密码可以
    _logout(client)
    bad = client.post("/api/auth/login", json={"email": email, "password": "password-123"})
    assert bad.status_code == 401
    ok = client.post("/api/auth/login", json={"email": email, "password": "new-password-456"})
    assert ok.status_code == 200, ok.text
    assert ok.json()["email_verified"] is False


def test_reset_token_is_single_use(client, invite):
    email = _new_user(client, invite, "once")
    _logout(client)
    token = _token_from_mail(
        client.post("/api/auth/forgot-password", json={"email": email}).json()["dev_link"]
    )
    first = client.post("/api/auth/reset-password", json={"token": token, "password": "first-pass-111"})
    assert first.status_code == 200, first.text
    second = client.post("/api/auth/reset-password", json={"token": token, "password": "second-pass-222"})
    assert second.status_code == 400
    assert "无效或已过期" in second.json()["detail"]


def test_expired_reset_token_rejected(client, invite):
    email = _new_user(client, invite, "expired")
    _logout(client)
    token = _token_from_mail(
        client.post("/api/auth/forgot-password", json={"email": email}).json()["dev_link"]
    )
    with SessionLocal() as db:
        row = db.query(EmailToken).filter(EmailToken.token_hash == token_hash(token)).one()
        row.expires_at = now() - timedelta(minutes=1)
        db.commit()
    r = client.post("/api/auth/reset-password", json={"token": token, "password": "whatever-333"})
    assert r.status_code == 400


def test_forgot_password_hides_unknown_email(client):
    _logout(client)
    email = f"nobody{MAIL_SUFFIX}"
    r = client.post("/api/auth/forgot-password", json={"email": email})
    assert r.status_code == 200
    assert r.json() == {"ok": True}
    # 不存在的邮箱不会留下可用令牌
    with SessionLocal() as db:
        rows = db.query(EmailToken).filter(EmailToken.email == email).all()
        assert rows and all(row.used_at and row.user_id == "" for row in rows)


def test_forgot_password_is_throttled(client, invite):
    email = _new_user(client, invite, "throttle")
    _logout(client)
    first = client.post("/api/auth/forgot-password", json={"email": email})
    assert first.status_code == 200
    second = client.post("/api/auth/forgot-password", json={"email": email})
    assert second.status_code == 429
    assert "频繁" in second.json()["detail"]


def test_reset_revokes_other_pending_links(client, invite):
    email = _new_user(client, invite, "multi")
    _logout(client)
    first = _token_from_mail(
        client.post("/api/auth/forgot-password", json={"email": email}).json()["dev_link"]
    )
    # 60 秒冷却会挡住第二次申请，这里直接写一条“上一封邮件”的旧令牌
    with SessionLocal() as db:
        user = db.query(User).filter(User.email == email).one()
        stale_raw = random_token(16)
        db.add(
            EmailToken(
                user_id=user.id,
                email=email,
                kind="reset_password",
                token_hash=token_hash(stale_raw),
                expires_at=now() + timedelta(minutes=30),
            )
        )
        db.commit()

    used = client.post("/api/auth/reset-password", json={"token": first, "password": "after-reset-444"})
    assert used.status_code == 200, used.text
    # 重置成功后，同一用户其它未使用的链接一并作废
    stale = client.post("/api/auth/reset-password", json={"token": stale_raw, "password": "stale-pass-555"})
    assert stale.status_code == 400


def test_register_sends_verification_mail(client, invite):
    """注册后自动发一封验证邮件（测试环境只落日志，但令牌要真的落库）。"""
    email = _new_user(client, invite, "autosend")
    with SessionLocal() as db:
        rows = (
            db.query(EmailToken)
            .filter(EmailToken.email == email, EmailToken.kind == "verify_email")
            .all()
        )
    assert len(rows) == 1
    assert rows[0].used_at is None


def test_email_verification_flow(client, invite):
    email = _new_user(client, invite, "verify")
    me = client.get("/api/auth/me").json()
    assert me["email_verified"] is False

    # 注册时已经自动发过一封，60 秒内再点「发送验证邮件」会被限流并给出提示
    throttled = client.post("/api/auth/send-verification")
    assert throttled.status_code == 429
    assert "刚刚已经发送" in throttled.json()["detail"]

    # 清掉自动发的那封，模拟冷却期过后重新发送
    with SessionLocal() as db:
        db.query(EmailToken).filter(
            EmailToken.email == email, EmailToken.kind == "verify_email"
        ).delete()
        db.commit()

    sent = client.post("/api/auth/send-verification")
    assert sent.status_code == 200, sent.text
    body = sent.json()
    assert body["already"] is False
    token = _token_from_mail(body["dev_link"])

    ok = client.post("/api/auth/verify-email", json={"token": token})
    assert ok.status_code == 200, ok.text
    assert client.get("/api/auth/me").json()["email_verified"] is True

    # 已验证后再发一次：直接告诉前端已经不需要发了
    again = client.post("/api/auth/send-verification")
    assert again.status_code == 200
    assert again.json()["already"] is True
    # 令牌只能用一次
    reuse = client.post("/api/auth/verify-email", json={"token": token})
    assert reuse.status_code == 400
