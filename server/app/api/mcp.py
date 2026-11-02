"""TOL-05：MCP 服务管理。

接口：
  GET    /api/mcp/servers          列表
  POST   /api/mcp/servers          新增（保存后立即检测工具）
  PATCH  /api/mcp/servers/{id}     修改（地址/令牌变化时重新检测）
  POST   /api/mcp/servers/{id}/test  重新检测
  DELETE /api/mcp/servers/{id}     删除（同时从 Bot / 技能里摘掉这些工具）
"""
from __future__ import annotations

from fastapi import APIRouter, Body, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.tools import mcp
from app.core.db import get_db
from app.core.deps import current_user, owned_or_404
from app.core.models import Bot, McpServer, Skill, User, now
from app.core.security import decrypt_secret, encrypt_secret

router = APIRouter(prefix="/mcp", tags=["mcp"])
LEVELS = ("L1", "L2", "L3")


class McpIn(BaseModel):
    name: str = Field(min_length=1, max_length=60)
    transport: str = "http"  # http | stdio
    url: str = ""
    command: str | None = None
    args: list[str] = []
    env: dict[str, str] = {}
    token: str | None = None
    level: str = "L3"
    enabled: bool = True


class McpPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=60)
    transport: str | None = None
    url: str | None = None
    command: str | None = None
    args: list[str] | None = None
    env: dict[str, str] | None = None
    token: str | None = None  # 空字符串表示清除
    level: str | None = None
    enabled: bool | None = None


def _out(s: McpServer) -> dict:
    return {
        "id": s.id,
        "name": s.name,
        "slug": s.slug,
        "transport": s.transport or "http",
        "url": s.url,
        "command": s.command,
        "args": s.args or [],
        "env": s.env or {},
        "level": s.level,
        "enabled": s.enabled,
        "status": s.status,
        "last_error": s.last_error,
        "last_checked_at": s.last_checked_at.isoformat() if s.last_checked_at else None,
        "token_last4": s.token_last4,
        "tool_count": len(s.tools or []),
        "tools": [
            {
                "name": row.get("name"),
                "remote_name": row.get("remote_name"),
                "description": row.get("description") or "",
            }
            for row in (s.tools or [])
            if isinstance(row, dict)
        ],
    }


def _unique_slug(db: Session, user_id: str, name: str, exclude_id: str | None = None) -> str:
    base = mcp.slugify(name, "mcp")
    slug = base
    i = 2
    while True:
        q = select(McpServer).where(McpServer.user_id == user_id, McpServer.slug == slug)
        if exclude_id:
            q = q.where(McpServer.id != exclude_id)
        if not db.scalar(q):
            return slug
        slug = f"{base[:8]}{i}"
        i += 1


def _clean_tools(db: Session, user_id: str, slug: str) -> None:
    """删除服务后，把 mcp__slug__* 从 Bot 与技能的工具列表里摘掉。"""
    prefix = f"mcp__{slug}__"
    for bot in db.scalars(select(Bot).where(Bot.user_id == user_id)).all():
        kept = [t for t in (bot.tools or []) if not str(t).startswith(prefix)]
        if kept != (bot.tools or []):
            bot.tools = kept
    for skill in db.scalars(select(Skill).where(Skill.user_id == user_id)).all():
        kept = [t for t in (skill.tools or []) if not str(t).startswith(prefix)]
        if kept != (skill.tools or []):
            skill.tools = kept


async def _refresh(server: McpServer) -> None:
    token = decrypt_secret(server.token_encrypted) if server.token_encrypted else None
    try:
        rows = await mcp.list_remote_tools(server, token)
        server.tools = mcp.cached_rows(server, rows)
        server.status = "ok"
        server.last_error = None
    except mcp.McpError as exc:
        server.status = "error"
        server.last_error = str(exc)[:500]
    server.last_checked_at = now()


def _check(level: str) -> None:
    if level not in LEVELS:
        raise HTTPException(400, "权限级别只能是 L1 / L2 / L3")


def _validate_transport(transport: str, url: str, command: str | None) -> None:
    if transport == "stdio":
        if not command or not command.strip():
            raise HTTPException(400, "stdio 传输需要填写启动命令")
    else:
        if not url or not url.strip():
            raise HTTPException(400, "HTTP 传输需要填写 MCP 地址")
        try:
            mcp._guard_url(url)
        except mcp.McpError as exc:
            raise HTTPException(400, str(exc))


async def _create_from_spec(db: Session, user: User, name: str, spec: dict) -> McpServer:
    """把一个 JSON 配置片段（Claude Desktop 风格）落成一个 MCP 服务并检测。"""
    if spec.get("command"):
        transport = "stdio"
        command = str(spec["command"]).strip()
        url = ""
        args = [str(a) for a in (spec.get("args") or [])]
        env = {str(k): str(v) for k, v in (spec.get("env") or {}).items()}
        token = None
    elif spec.get("url"):
        transport = "http"
        url = str(spec["url"]).strip()
        command = None
        args = []
        env = {}
        token = spec.get("token")
        if not token:
            headers = spec.get("headers") or {}
            auth = headers.get("Authorization") or headers.get("authorization") or ""
            if str(auth).lower().startswith("bearer "):
                token = str(auth)[7:].strip()
    else:
        raise HTTPException(400, "缺少 command 或 url")

    level = str(spec.get("level") or "L3").upper()
    _check(level)
    enabled = bool(spec.get("enabled", True))
    _validate_transport(transport, url, command)

    server = McpServer(
        user_id=user.id,
        name=name,
        slug=_unique_slug(db, user.id, name),
        transport=transport,
        url=url,
        command=command,
        args=args,
        env=env,
        level=level,
        enabled=enabled,
        token_encrypted=encrypt_secret(token) if token else None,
        token_last4=token[-4:] if token else None,
    )
    db.add(server)
    db.flush()
    await _refresh(server)
    db.flush()
    return server


@router.get("/servers")
def list_servers(user: User = Depends(current_user), db: Session = Depends(get_db)):
    rows = db.scalars(
        select(McpServer).where(McpServer.user_id == user.id).order_by(McpServer.created_at)
    ).all()
    return [_out(s) for s in rows]


@router.post("/import")
async def import_servers(
    payload: dict = Body(...),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    """从 JSON 批量导入 MCP 服务（Claude Desktop 的 mcpServers 风格）。

    支持两种包裹：`{"mcpServers": {...}}` 或直接顶层对象 `{name: spec}`。
    返回 created / skipped（重名）/ errors 三类。
    """
    servers = payload.get("mcpServers") if isinstance(payload, dict) and "mcpServers" in payload else payload
    if not isinstance(servers, dict):
        raise HTTPException(400, "JSON 顶层应为 mcpServers 对象")
    created: list[dict] = []
    skipped: list[dict] = []
    errors: list[dict] = []
    for raw_name, spec in servers.items():
        name = str(raw_name).strip()
        if db.scalar(
            select(McpServer).where(McpServer.user_id == user.id, McpServer.name == name)
        ):
            skipped.append({"name": name, "reason": "已存在同名服务"})
            continue
        if not isinstance(spec, dict):
            errors.append({"name": name, "reason": "配置不是对象"})
            continue
        try:
            server = await _create_from_spec(db, user, name, spec)
            created.append(_out(server))
        except HTTPException as exc:
            errors.append({"name": name, "reason": str(exc.detail)})
        except mcp.McpError as exc:
            errors.append({"name": name, "reason": str(exc)})
    return {"created": created, "skipped": skipped, "errors": errors}


@router.post("/servers", status_code=201)
async def create_server(
    payload: McpIn, user: User = Depends(current_user), db: Session = Depends(get_db)
):
    _check(payload.level)
    _validate_transport(payload.transport, payload.url, payload.command)
    name = payload.name.strip()
    if db.scalar(
        select(McpServer).where(McpServer.user_id == user.id, McpServer.name == name)
    ):
        raise HTTPException(400, f"已存在名为「{name}」的 MCP 服务")
    server = McpServer(
        user_id=user.id,
        name=name,
        slug=_unique_slug(db, user.id, name),
        transport=payload.transport,
        url=(payload.url or "").strip(),
        command=(payload.command or "").strip() or None,
        args=payload.args or [],
        env=payload.env or {},
        level=payload.level,
        enabled=payload.enabled,
        token_encrypted=encrypt_secret(payload.token) if payload.token else None,
        token_last4=payload.token[-4:] if payload.token else None,
    )
    db.add(server)
    db.flush()
    await _refresh(server)
    db.flush()
    return _out(server)


@router.patch("/servers/{server_id}")
async def update_server(
    server_id: str,
    payload: McpPatch,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    server = owned_or_404(db, McpServer, server_id, user.id)
    if payload.name is not None:
        name = payload.name.strip()
        if name != server.name and db.scalar(
            select(McpServer).where(
                McpServer.user_id == user.id,
                McpServer.name == name,
                McpServer.id != server.id,
            )
        ):
            raise HTTPException(400, f"已存在名为「{name}」的 MCP 服务")
        server.name = name
    if payload.level is not None:
        _check(payload.level)
        server.level = payload.level
    if payload.enabled is not None:
        server.enabled = payload.enabled
    reprobe = False
    if payload.transport is not None and payload.transport != (server.transport or "http"):
        if payload.transport not in ("http", "stdio"):
            raise HTTPException(400, "传输类型只能是 http 或 stdio")
        server.transport = payload.transport
        reprobe = True
    if payload.url is not None and payload.url.strip() != server.url:
        server.url = payload.url.strip()
        reprobe = True
    if payload.command is not None:
        server.command = payload.command.strip() or None
        reprobe = True
    if payload.args is not None:
        server.args = payload.args
        reprobe = True
    if payload.env is not None:
        server.env = payload.env
        reprobe = True
    # 传输相关的必填校验（用合并后的最新值）
    _validate_transport(
        server.transport or "http",
        payload.url if payload.url is not None else server.url,
        payload.command if payload.command is not None else server.command,
    )
    if payload.token is not None:
        if payload.token:
            server.token_encrypted = encrypt_secret(payload.token)
            server.token_last4 = payload.token[-4:]
        else:
            server.token_encrypted = None
            server.token_last4 = None
        reprobe = True
    if reprobe:
        server.status = "unchecked"
        await _refresh(server)
    db.flush()
    return _out(server)


@router.post("/servers/{server_id}/test")
async def test_server(
    server_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)
):
    server = owned_or_404(db, McpServer, server_id, user.id)
    await _refresh(server)
    db.flush()
    return {
        "ok": server.status == "ok",
        "status": server.status,
        "error": server.last_error,
        "tools": _out(server)["tools"],
    }


@router.delete("/servers/{server_id}")
def delete_server(
    server_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)
):
    server = owned_or_404(db, McpServer, server_id, user.id)
    _clean_tools(db, user.id, server.slug)
    db.delete(server)
    db.flush()
    return {"ok": True}
