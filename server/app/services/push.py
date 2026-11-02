"""NTF-06 Web Push（Service Worker + VAPID）。

页面关闭后，浏览器通知走推送服务：后端持 VAPID 密钥向订阅端点发加密消息，
Service Worker 收到后弹出系统通知。

- VAPID 密钥优先读环境变量 `BOTLY_VAPID_PRIVATE_KEY`（PEM 或 32 字节原始私钥的
  base64url）；没有配置时首次自动生成并持久化到 `data/vapid.json`，重启不变。
- 订阅存 `push_subscriptions` 表（按端点去重）；推送返回 404/410 时视为失效并清理。
- 发送在后台线程进行：不阻塞请求事务，失败只记日志，不影响通知落库与 SSE。
"""
from __future__ import annotations

import base64
import json
import logging
import threading
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import DATA_DIR, PUSH_TTL, VAPID_PRIVATE_KEY, VAPID_SUBJECT
from app.core.db import SessionLocal
from app.core.models import PushSubscription, UserSettings, now

logger = logging.getLogger("push")

_KEY_FILE = DATA_DIR / "vapid.json"

_cached: dict[str, Any] | None = None
_lock = threading.Lock()


# ---- VAPID 密钥 ----

def _generate_keys() -> dict[str, Any]:
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ec

    key = ec.generate_private_key(ec.SECP256R1())
    private_pem = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()
    public = key.public_key().public_bytes(
        serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint
    )
    return {
        "private_pem": private_pem,
        "public": base64.urlsafe_b64encode(public).rstrip(b"=").decode(),
    }


def _keys_from_env() -> dict[str, Any] | None:
    raw = VAPID_PRIVATE_KEY.strip()
    if not raw:
        return None
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ec

    if raw.startswith("-----BEGIN"):
        pem = raw
    else:
        # 允许直接给 32 字节原始私钥的 base64url/base64
        pad = "=" * (-len(raw) % 4)
        try:
            scalar = base64.urlsafe_b64decode(raw + pad)
        except Exception:
            logger.warning("BOTLY_VAPID_PRIVATE_KEY 不是有效的 PEM 或 base64，已忽略")
            return None
        try:
            key = ec.derive_private_key(int.from_bytes(scalar, "big"), ec.SECP256R1())
        except Exception:
            logger.warning("BOTLY_VAPID_PRIVATE_KEY 解码后不是有效的 P-256 私钥，已忽略")
            return None
        pem = key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        ).decode()
    priv = serialization.load_pem_private_key(pem.encode(), password=None)
    pub = priv.public_key().public_bytes(
        serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint
    )
    return {
        "private_pem": pem,
        "public": base64.urlsafe_b64encode(pub).rstrip(b"=").decode(),
    }


def keys() -> dict[str, Any]:
    """返回 {private_pem, public}；环境变量优先，否则自动生成并落盘。"""
    global _cached
    with _lock:
        if _cached is not None:
            return _cached
        data = _keys_from_env()
        if data is None:
            try:
                data = json.loads(_KEY_FILE.read_text())
            except Exception:
                data = None
        if not data or "private_pem" not in data:
            data = _generate_keys()
            try:
                _KEY_FILE.write_text(json.dumps(data))
                try:
                    _KEY_FILE.chmod(0o600)
                except OSError:
                    pass
                logger.info("已生成 VAPID 密钥：%s", _KEY_FILE)
            except Exception:  # noqa: BLE001 - 只读盘不阻塞启动
                logger.warning("VAPID 密钥写入失败（%s），本次运行仍可用", _KEY_FILE)
        _cached = data
        return _cached


def public_key() -> str:
    """PushManager.subscribe 用的 applicationServerKey（base64url）。"""
    return keys()["public"]


def _vapid_instance():
    from py_vapid import Vapid

    pem = keys()["private_pem"]
    try:
        return Vapid.from_string(pem)
    except Exception:  # noqa: BLE001 - 兼容不同 py-vapid 版本
        return Vapid.from_pem(pem.encode())


# ---- 发送 ----

def send_to_subscription(
    subscription: dict[str, Any], payload: str, ttl: int = PUSH_TTL
) -> None:
    """向单个订阅端点发送加密推送。抛 WebPushException 表示失败。

    独立成模块级函数，方便测试替身。
    """
    from pywebpush import webpush

    webpush(
        subscription_info={
            "endpoint": subscription["endpoint"],
            "keys": {"p256dh": subscription["p256dh"], "auth": subscription["auth"]},
        },
        data=payload,
        vapid_private_key=_vapid_instance(),
        vapid_claims={"sub": VAPID_SUBJECT if "@" in VAPID_SUBJECT else f"mailto:{VAPID_SUBJECT}"},
        ttl=ttl,
        timeout=10,
    )


def _deliver(user_id: str, payload: str, notif_type: str) -> None:
    """后台线程：查订阅并逐个发送；404/410 的订阅清理掉。"""
    dead: list[str] = []
    try:
        with SessionLocal() as db:
            setting = db.get(UserSettings, user_id)
            # 与页面内通知共用同一个总开关（NTF-03）
            if setting and setting.notifications_enabled is False:
                return
            subs = db.scalars(
                select(PushSubscription).where(PushSubscription.user_id == user_id)
            ).all()
            rows = [
                {
                    "endpoint": s.endpoint,
                    "p256dh": s.p256dh,
                    "auth": s.auth,
                }
                for s in subs
            ]
            for row in rows:
                try:
                    send_to_subscription(row, payload)
                except Exception as exc:  # noqa: BLE001
                    status = getattr(exc, "response", None)
                    code = getattr(status, "status_code", None) if status is not None else None
                    if code in (404, 410):
                        dead.append(row["endpoint"])
                    else:
                        logger.warning(
                            "Web Push 发送失败（type=%s, code=%s）: %s", notif_type, code, exc
                        )
            if dead:
                for s in db.scalars(
                    select(PushSubscription).where(PushSubscription.endpoint.in_(dead))
                ).all():
                    db.delete(s)
                db.commit()
                logger.info("已清理 %d 个失效推送订阅", len(dead))
    except Exception:  # noqa: BLE001 - 推送失败不影响通知主流程
        logger.exception("Web Push 后台发送异常")


def push_notification(
    user_id: str,
    notif_id: str,
    type_: str,
    title: str,
    body: str = "",
    chat_id: str | None = None,
    target_type: str | None = None,
    target_id: str | None = None,
) -> None:
    """把一条通知投递到该用户的所有 Web Push 订阅（后台线程，立即返回）。"""
    payload = json.dumps(
        {
            "id": notif_id,
            "type": type_,
            "title": title or "Botly",
            "body": body,
            "chat_id": chat_id,
            "target_type": target_type,
            "target_id": target_id,
            "created_at": now().isoformat(),
        },
        ensure_ascii=False,
    )
    threading.Thread(
        target=_deliver, args=(user_id, payload, type_), name="web-push", daemon=True
    ).start()
