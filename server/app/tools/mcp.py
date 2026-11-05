"""TOL-05：MCP（Model Context Protocol）工具接入。

第一版只支持 Streamable HTTP：
  POST <url>  JSON-RPC 2.0
  initialize → notifications/initialized → tools/list / tools/call

工具名对外暴露为 `mcp__<slug>__<tool>`，`slug` 由服务名推导并在创建时固化。
远端返回一律按不可信外部内容处理（与网页读取同一套标注）。
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import time
from urllib.parse import urlparse

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session
from app.core.config import ADMIN_EMAILS, MCP_ALLOW_LOCAL, MCP_MAX_RESULT_CHARS, MCP_MAX_TOOLS, MCP_TIMEOUT, MCP_STDIO_ENABLED, MCP_STDIO_ALLOWED_COMMANDS
from app.core.db import SessionLocal
from app.core.models import McpServer, User
from app.tools.registry import Tool, ToolError, UNTRUSTED_PREFIX, _check_url

PROTOCOL_VERSION = "2024-11-05"
_LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}


class McpError(Exception):
    pass


# --------------------------------------------------------------------------- #
# 命名
# --------------------------------------------------------------------------- #
def slugify(name: str, fallback: str) -> str:
    """把服务名压成工具名前缀：只保留字母数字，最长 10 位。"""
    raw = re.sub(r"[^a-zA-Z0-9]+", "", name or "").lower()
    if not raw:
        raw = fallback[:6]
    return raw[:10]


def safe_tool_name(name: str) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9_-]+", "_", name or "").strip("_")
    return cleaned or "tool"


def exposed_name(slug: str, remote: str) -> str:
    """OpenAI 工具名最长 64 字符，超长时截断并加短哈希避免重名。"""
    prefix = f"mcp__{slug}__"
    tool = safe_tool_name(remote)
    if len(prefix) + len(tool) <= 64:
        return prefix + tool
    digest = hashlib.sha1(tool.encode("utf-8")).hexdigest()[:6]
    keep = max(1, 64 - len(prefix) - len(digest) - 1)
    return f"{prefix}{tool[:keep]}_{digest}"


# --------------------------------------------------------------------------- #
# HTTP / JSON-RPC
# --------------------------------------------------------------------------- #
def _guard_url(url: str) -> None:
    p = urlparse(url)
    if p.scheme not in ("http", "https") or not p.hostname:
        raise McpError("MCP 地址必须是完整的 http/https URL")
    if MCP_ALLOW_LOCAL and (p.hostname or "").lower() in _LOCAL_HOSTS:
        return
    try:
        _check_url(url)
    except ToolError as exc:
        raise McpError(str(exc)) from exc


def _parse_body(text: str, content_type: str) -> dict:
    """兼容两种响应：普通 JSON 与 text/event-stream（每行 data: {...}）。"""
    if not text.strip():
        return {}
    if "text/event-stream" in (content_type or "").lower():
        payloads = []
        for line in text.splitlines():
            if not line.startswith("data:"):
                continue
            chunk = line[5:].strip()
            if not chunk or chunk == "[DONE]":
                continue
            try:
                payloads.append(json.loads(chunk))
            except json.JSONDecodeError:
                continue
        if not payloads:
            raise McpError("MCP 返回了无法解析的事件流")
        # 取最后一条带 result/error 的响应（通知没有 id）
        for item in reversed(payloads):
            if isinstance(item, dict) and ("result" in item or "error" in item):
                return item
        return payloads[-1]
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise McpError(f"MCP 返回的不是 JSON：{text[:160]}") from exc


async def _post(
        url: str, payload: dict, token: str | None, session_id: str | None = None
) -> tuple[dict, str | None]:
    await asyncio.to_thread(_guard_url, url)
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
        "MCP-Protocol-Version": PROTOCOL_VERSION,
        "User-Agent": "Botly/1.0 MCP-Client",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if session_id:
        headers["Mcp-Session-Id"] = session_id
    try:
        async with httpx.AsyncClient(timeout=MCP_TIMEOUT, follow_redirects=False) as client:
            r = await client.post(url, headers=headers, json=payload)
    except httpx.HTTPError as exc:
        raise McpError(f"连接 MCP 服务失败：{type(exc).__name__}: {exc}") from exc
    if r.status_code in (401, 403):
        raise McpError(f"MCP 服务拒绝了访问（HTTP {r.status_code}），请检查访问令牌")
    if r.status_code >= 400:
        raise McpError(f"MCP 服务返回 HTTP {r.status_code}：{r.text[:200]}")
    body = _parse_body(r.text, r.headers.get("content-type") or "")
    if isinstance(body, dict) and body.get("error"):
        err = body["error"]
        message = err.get("message") if isinstance(err, dict) else str(err)
        raise McpError(f"MCP 错误：{message or '未知错误'}")
    return body, r.headers.get("mcp-session-id")


async def _open_session(url: str, token: str | None) -> str | None:
    body, session_id = await _post(
        url,
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {"name": "botly", "version": "1.0"},
            },
        },
        token,
    )
    if not body.get("result"):
        raise McpError("MCP 服务没有返回初始化结果")
    # 规范要求初始化后发一条通知；部分服务对 202/204 空响应即可
    try:
        await _post(
            url,
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            token,
            session_id,
        )
    except McpError:
        pass
    return session_id


def _normalize_tools(rows: list | None) -> list[dict]:
    if not isinstance(rows, list):
        raise McpError("MCP 服务没有返回工具列表")
    out: list[dict] = []
    for row in rows[:MCP_MAX_TOOLS]:
        if not isinstance(row, dict) or not row.get("name"):
            continue
        schema = row.get("inputSchema") or row.get("input_schema") or {}
        if not isinstance(schema, dict) or schema.get("type") != "object":
            schema = {"type": "object", "properties": {}}
        out.append(
            {
                "remote_name": str(row["name"]),
                "description": str(row.get("description") or "")[:800],
                "parameters": schema,
            }
        )
    return out


def _format_call_result(result: dict) -> str:
    if not isinstance(result, dict):
        raise McpError("MCP 工具没有返回结果")
    parts: list[str] = []
    for item in result.get("content") or []:
        if not isinstance(item, dict):
            continue
        if item.get("type") == "text":
            parts.append(str(item.get("text") or ""))
        elif item.get("type") == "resource":
            res = item.get("resource") or {}
            parts.append(f"[资源] {res.get('uri') or ''}\n{str(res.get('text') or '')}")
        else:
            parts.append(f"[{item.get('type') or '内容'}] {json.dumps(item, ensure_ascii=False)[:500]}")
    text = "\n".join(p for p in parts if p).strip() or "（MCP 工具没有返回文本内容）"
    if result.get("isError"):
        return f"MCP 工具返回错误：{text[:1000]}"
    if len(text) > MCP_MAX_RESULT_CHARS:
        text = text[:MCP_MAX_RESULT_CHARS] + "\n\n[内容已截断]"
    return UNTRUSTED_PREFIX + text


async def _http_list_tools(url: str, token: str | None) -> list[dict]:
    session_id = await _open_session(url, token)
    body, _ = await _post(
        url,
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
        token,
        session_id,
    )
    return _normalize_tools((body.get("result") or {}).get("tools"))


async def _http_call(url: str, token: str | None, remote_name: str, args: dict) -> str:
    session_id = await _open_session(url, token)
    body, _ = await _post(
        url,
        {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {"name": remote_name, "arguments": args or {}},
        },
        token,
        session_id,
    )
    return _format_call_result(body.get("result") or {})


# --------------------------------------------------------------------------- #
# stdio 传输：本地进程 + 换行分隔 JSON-RPC
# --------------------------------------------------------------------------- #
def _init_payload(msg_id: int) -> dict:
    return {
        "jsonrpc": "2.0",
        "id": msg_id,
        "method": "initialize",
        "params": {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {},
            "clientInfo": {"name": "botly", "version": "1.0"},
        },
    }


def validate_stdio(command: str | None, args: list | None) -> None:
    if not MCP_STDIO_ENABLED:
        raise McpError("MCP stdio 传输已禁用")
    argv = [command, *(args or [])]
    if not command or not os.path.isabs(command):
        raise McpError("MCP stdio 命令必须使用绝对路径")
    if any(not isinstance(arg, str) or re.search(r"[\x00\r\n;&|`$<>]", arg) for arg in argv):
        raise McpError("MCP stdio 命令参数不能包含 shell 元字符")
    if argv not in MCP_STDIO_ALLOWED_COMMANDS:
        raise McpError("MCP stdio 命令及参数不在部署白名单中")


def _guard_stdio_server(server: McpServer) -> None:
    validate_stdio(server.command, server.args)
    with SessionLocal() as db:
        owner = db.get(User, server.user_id)
        if not owner or owner.status != "active" or owner.email.lower() not in ADMIN_EMAILS:
            raise McpError("仅管理员可状态 MCP stdio 服务")


async def _spawn_stdio(command: str, args: list, env: dict):
    validate_stdio(command, args)
    full_env = {"PATH": os.defpath, "HOME": os.path.expanduser("~")}
    full_env.update({str(k): str(v) for k, v in (env or {}).items()})
    cmd = [command, *(args or [])]
    try:
        return await asyncio.create_subprocess_exec(
            *cmd,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=full_env,
        )
    except FileNotFoundError:
        raise McpError(f"找不到命令：{command}。请确认命令在 PATH 中，或用绝对路径。")
    except OSError as exc:
        raise McpError(f"启动 MCP 进程失败：{exc}")


async def _stdio_request(proc, payload: dict) -> dict:
    try:
        proc.stdin.write((json.dumps(payload) + "\n").encode())
        await proc.stdin.drain()
    except Exception as exc:  # noqa: BLE001
        raise McpError(f"写入 MCP 进程失败：{exc}")
    deadline = time.monotonic() + MCP_TIMEOUT
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise McpError("MCP 进程响应超时")
        try:
            raw = await asyncio.wait_for(proc.stdout.readline(), remaining)
        except asyncio.TimeoutError:
            raise McpError("MCP 进程响应超时")
        if not raw:
            raise McpError("MCP 进程提前退出")
        line = raw.decode("utf-8", errors="replace").strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(msg, dict) and msg.get("id") == payload.get("id"):
            if msg.get("error"):
                err = msg["error"]
                raise McpError(f"MCP 错误：{err.get('message') if isinstance(err, dict) else err}")
            return msg.get("result", {})


async def _stdio_session(command: str, args: list, env: dict, method: str, params: dict) -> dict:
    proc = await _spawn_stdio(command, args, env)
    try:
        await _stdio_request(proc, _init_payload(1))
        # initialized 通知（无 id，fire-and-forget）
        try:
            proc.stdin.write(b'{"jsonrpc":"2.0","method":"notifications/initialized"}\n')
            await proc.stdin.drain()
        except Exception:  # noqa: BLE001
            pass
        return await _stdio_request(
            proc, {"jsonrpc": "2.0", "id": 2, "method": method, "params": params}
        )
    finally:
        try:
            proc.kill()
            await proc.wait()
        except Exception:  # noqa: BLE001
            pass


async def _stdio_list_tools(command: str, args: list, env: dict) -> list[dict]:
    result = await _stdio_session(command, args, env, "tools/list", {})
    return _normalize_tools(result.get("tools"))


async def _stdio_call(command: str, args: list, env: dict, remote_name: str, call_args: dict) -> str:
    result = await _stdio_session(
        command, args, env, "tools/call", {"name": remote_name, "arguments": call_args or {}}
    )
    return _format_call_result(result)


async def list_remote_tools(server: McpServer, token: str | None) -> list[dict]:
    """tools/list：返回 [{remote_name, description, parameters}]。"""
    if (server.transport or "http") == "stdio":
        await asyncio.to_thread(_guard_stdio_server, server)
        return await _stdio_list_tools(server.command, server.args or [], server.env or {})
    return await _http_list_tools(server.url, token)


async def call_remote_tool(
    server: McpServer, token: str | None, remote_name: str, args: dict
) -> str:
    if (server.transport or "http") == "stdio":
        await asyncio.to_thread(_guard_stdio_server, server)
        return await _stdio_call(server.command, server.args or [], server.env or {}, remote_name, args)
    return await _http_call(server.url, token, remote_name, args)


# --------------------------------------------------------------------------- #
# 本地缓存 → Tool 对象
# --------------------------------------------------------------------------- #
def _tool_from_row(server: McpServer, row: dict) -> Tool:
    remote = str(row.get("remote_name") or "")
    desc = str(row.get("description") or "").strip()
    return Tool(
        name=str(row.get("name") or exposed_name(server.slug, remote)),
        description=f"[MCP · {server.name}] {desc}".strip(),
        parameters=row.get("parameters") or {"type": "object", "properties": {}},
        level=server.level or "L3",
        kind="mcp",
        executor=None,
        server_id=server.id,
        remote_name=remote,
    )


def tools_for_user(db: Session, user_id: str) -> list[Tool]:
    """该用户所有启用服务里的工具（已检测过的缓存）。"""
    servers = db.scalars(
        select(McpServer).where(McpServer.user_id == user_id, McpServer.enabled.is_(True))
    ).all()
    out: list[Tool] = []
    owner = db.get(User, user_id)
    for server in servers:
        if (server.transport or "http") == "stdio":
            if not owner or owner.email.lower() not in ADMIN_EMAILS:
                continue
            try:
                validate_stdio(server.command, server.args)
            except McpError:
                continue
        names: set[str] = set()
        for row in server.tools or []:
            if not isinstance(row, dict) or not row.get("remote_name"):
                continue
            tool = _tool_from_row(server, row)
            if tool.name in names:
                continue
            names.add(tool.name)
            out.append(tool)
    return out


def find_tool(db: Session, user_id: str, name: str) -> Tool | None:
    return next((t for t in tools_for_user(db, user_id) if t.name == name), None)


def cached_rows(server: McpServer, remote_tools: list[dict]) -> list[dict]:
    """检测结果落库：固化对外工具名，避免改名后 Bot 勾选失效。"""
    rows = []
    for item in remote_tools:
        remote = item["remote_name"]
        rows.append(
            {
                "name": exposed_name(server.slug, remote),
                "remote_name": remote,
                "description": item.get("description") or "",
                "parameters": item.get("parameters") or {"type": "object", "properties": {}},
            }
        )
    return rows
