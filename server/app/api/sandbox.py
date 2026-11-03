"""用户自己的 CubeSandbox 后端连接管理。"""
from __future__ import annotations

import asyncio
import ipaddress
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.deps import current_user
from app.core.models import CubeConnection, CubeSession, User, now
from app.core.security import encrypt_secret
from app.sandbox import cube, runtime

router = APIRouter(prefix="/sandbox", tags=["sandbox"])


class CubeIn(BaseModel):
    api_url: str = Field(min_length=1, max_length=500)
    template_id: str = Field(min_length=1, max_length=200)
    api_key: str | None = Field(default=None, max_length=2000)
    proxy_node_ip: str | None = Field(default=None, max_length=200)
    proxy_port: int = Field(default=80, ge=1, le=65535)
    sandbox_domain: str = Field(default="cube.app", min_length=1, max_length=200)


def candidate(payload: CubeIn, user_id: str, existing: CubeConnection | None) -> CubeConnection:
    url = payload.api_url.strip().rstrip("/")
    parsed = urlsplit(url)
    try:
        parsed.port
    except ValueError:
        raise HTTPException(400, "Cube 后端端口无效")
    if parsed.scheme not in ("http", "https") or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise HTTPException(400, "Cube 后端地址应为 http/https URL，不能包含账号密码、查询参数或片段")
    # 用户明确接入自己部署的后端，允许本机/内网地址，区别于网页读取工具。
    proxy = (payload.proxy_node_ip or "").strip() or None
    proxy_port = payload.proxy_port
    if proxy:
        if "://" in proxy:
            address = urlsplit(proxy)
            try:
                address_port = address.port
            except ValueError:
                raise HTTPException(400, "代理节点端口无效")
            if (address.scheme not in ("http", "https") or not address.hostname
                or address.username is not None or address.password is not None
                or address.path not in ("", "/") or address.query or address.fragment):
                raise HTTPException(400, "代理节点填写 IP 地址或 http/https IP 地址，不能包含路径、账号密码或查询参数")
            proxy = address.hostname
            if address_port is not None:
                if not 1 <= address_port <= 65535:
                    raise HTTPException(400, "代理节点端口无效")
                if "proxy_port" in payload.model_fields_set and proxy_port != address_port:
                    raise HTTPException(400, "代理地址中的端口与单独填写的代理端口不一致")
                proxy_port = address_port
        try:
            proxy = str(ipaddress.ip_address(proxy))
        except ValueError:
            raise HTTPException(400, "代理节点填写 IP 地址，端口单独填写")
    domain = payload.sandbox_domain.strip()
    if any(c in domain for c in ("/", ":", " ", "?", "#")):
        raise HTTPException(400, "沙箱域名只填写域名，例如 cube.app")
    template = payload.template_id.strip()
    if not template:
        raise HTTPException(400, "模板 ID 不能为空")
    encrypted = existing.api_key_encrypted if existing else None
    if payload.api_key is not None:
        encrypted = encrypt_secret(payload.api_key.strip())
    return CubeConnection(user_id=user_id, api_url=url, template_id=template, api_key_encrypted=encrypted,
        proxy_node_ip=proxy, proxy_port=proxy_port, sandbox_domain=domain)


@router.get("")
def get_sandbox(user: User = Depends(current_user)):
    return runtime.status(user.id)


@router.put("/cube")
def save_cube(payload: CubeIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    existing = db.scalar(select(CubeConnection).where(CubeConnection.user_id == user.id))
    updated = candidate(payload, user.id, existing)
    if existing and cube.fingerprint(existing) != cube.fingerprint(updated):
        bound = db.scalar(select(CubeSession.id).where(CubeSession.user_id == user.id).limit(1))
        if bound:
            raise HTTPException(409, "已有群聊绑定当前 Cube 后端，请先断开并释放旧沙箱，再保存新连接")
    if existing:
        changed = not cube.same_config(existing, updated)
        for key in ("api_url", "template_id", "api_key_encrypted", "proxy_node_ip", "proxy_port", "sandbox_domain"):
            setattr(existing, key, getattr(updated, key))
        if changed:
            existing.last_checked_at = None
            existing.last_check_error = None
    else:
        db.add(updated)
    db.commit()
    return runtime.status(user.id)


@router.post("/cube/test")
async def test_cube(payload: CubeIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    existing = db.scalar(select(CubeConnection).where(CubeConnection.user_id == user.id))
    connection = candidate(payload, user.id, existing)
    db.commit()  # 远端测试不占数据库事务。
    try:
        result = await asyncio.to_thread(cube.probe, connection)
    except Exception as exc:
        error = cube.safe_error(exc, connection)
        current = db.scalar(select(CubeConnection).where(CubeConnection.user_id == user.id).execution_options(populate_existing=True))
        if current and cube.same_config(current, connection):
            current.last_checked_at, current.last_check_error = now(), error
            db.commit()
        raise HTTPException(400, "Cube 连接测试失败：" + error)
    current = db.scalar(select(CubeConnection).where(CubeConnection.user_id == user.id).execution_options(populate_existing=True))
    if current and cube.same_config(current, connection):
        current.last_checked_at, current.last_check_error = now(), None
        db.commit()
    return {**result, "sandbox": runtime.status(user.id)}


@router.delete("/cube")
async def disconnect_cube(user: User = Depends(current_user), db: Session = Depends(get_db)):
    connection = db.scalar(select(CubeConnection).where(CubeConnection.user_id == user.id))
    if not connection:
        return {"ok": True}
    from app.core.models import Run
    active = db.scalar(select(Run.id).where(Run.user_id == user.id, Run.status.in_(("running", "queued", "waiting", "waiting_children"))).limit(1))
    if active:
        raise HTTPException(409, "请先停止或完成当前执行，再断开沙箱连接")
    chats = list(db.scalars(select(CubeSession.chat_id).where(CubeSession.user_id == user.id)))
    db.commit()
    backend = cube.CubeBackend(connection)
    try:
        for chat_id in chats:
            await backend.release(user.id, chat_id)
    except Exception as exc:
        raise HTTPException(400, "旧 Cube 沙箱释放失败，连接已保留：" + cube.safe_error(exc, connection))
    db.execute(delete(CubeConnection).where(CubeConnection.user_id == user.id))
    db.commit()
    return {"ok": True}
