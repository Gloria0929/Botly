"""HTTP 请求日志中间件与统一异常处理。

每条请求记录一行 API 日志：方法、路径、状态码、耗时、响应大小、用户、IP、UA，
可选附带脱敏后的查询参数与请求/响应体（BOTLY_LOG_BODY=0 可关闭）。
500 异常在这里记录完整堆栈，并把 request_id 写进响应头 X-Request-Id，
回给用户的错误信息里也带上它，方便按 id 去日志里定位。

实现为纯 ASGI 中间件：只在 http.response.start 时记下状态码、统计
http.response.body 的字节数，不缓冲也不重建响应，因此不会破坏流式响应
（SSE）或 Content-Length / Content-Encoding。
"""
from __future__ import annotations

import time

from fastapi import Request
from fastapi.responses import JSONResponse

from . import logging_setup as logs
from .config import LOG_BODY, SESSION_COOKIE
from .security import read_session

log = logs.get_logger("http")
_ACCESS = logs.get_logger("access")

_BODY_METHODS = ("POST", "PUT", "PATCH", "DELETE")


def _client_ip(scope) -> str:
    headers = {k.decode("latin-1"): v.decode("latin-1") for k, v in scope.get("headers") or []}
    fwd = headers.get("x-forwarded-for")
    if fwd:
        return fwd.split(",")[0].strip()
    client = scope.get("client")
    return client[0] if client else "unknown"


def _headers(scope) -> dict[str, str]:
    return {k.decode("latin-1"): v.decode("latin-1") for k, v in scope.get("headers") or []}


def _user_id(scope) -> str | None:
    # 鉴权依赖在 endpoint 内部解析，中间件拿不到；用同一套规则再读一次会话
    raw = _headers(scope).get("cookie", "")
    for part in raw.split(";"):
        name, _, value = part.strip().partition("=")
        if name == SESSION_COOKIE and value:
            try:
                return read_session(value)
            except Exception:
                return None
    return None


def _body_summary(body: bytes) -> str:
    if not LOG_BODY or not body:
        return ""
    preview = logs.body_preview(body)
    return f" body={preview}" if preview else ""


class RequestLogMiddleware:
    """记录请求/响应摘要；不修改响应内容，只在 500 时补 X-Request-Id。"""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        path: str = scope.get("path", "")
        method: str = scope.get("method", "")
        headers = _headers(scope)
        rid = headers.get("x-request-id") or logs.new_request_id()
        token = logs.request_id_var.set(rid)
        started = time.perf_counter()
        is_stream = path.endswith("/events")

        # 只对会带 body 的方法取前一段，用于脱敏摘要；不阻塞、不重读
        body_chunks = b""
        inner_receive = receive
        if LOG_BODY and not is_stream and method in _BODY_METHODS:
            captured: list[bytes] = []

            async def recv():
                nonlocal body_chunks
                message = await receive()
                if message.get("type") == "http.request" and len(body_chunks) < 4096:
                    captured.append(message.get("body", b""))
                    body_chunks = b"".join(captured)[:4096]
                return message

            inner_receive = recv

        status = 500
        size = 0
        failed = False

        async def wrapped_send(message):
            nonlocal status, size
            if message["type"] == "http.response.start":
                status = message["status"]
                if status >= 500:
                    raw = message.setdefault("headers", [])
                    if not any(k.lower() == b"x-request-id" for k, _ in raw):
                        raw.append((b"x-request-id", rid.encode()))
            elif message["type"] == "http.response.body":
                size += len(message.get("body", b""))
            await send(message)

        try:
            await self.app(scope, inner_receive, wrapped_send)
        except Exception:
            failed = True
            raise
        finally:
            elapsed = (time.perf_counter() - started) * 1000
            query = scope.get("query_string", b"").decode("latin-1")
            ua = headers.get("user-agent", "")[:160]
            uid = logs.fingerprint(_user_id(scope))
            extra = ""
            if LOG_BODY and query:
                extra += f" query={query[:500]}"
            extra += _body_summary(body_chunks)
            level = _ACCESS.warning if status >= 500 else (_ACCESS.info if status >= 400 else _ACCESS.debug)
            level(
                "%s %s -> %d %.0fms size=%s ip=%s uid=%s%s%s",
                method,
                path,
                status,
                elapsed,
                "stream" if is_stream else size,
                _client_ip(scope),
                uid,
                f" ua={ua!r}" if ua else "",
                extra,
            )
            # 异常时 Starlette 外层的 ServerErrorMiddleware 负责渲染 500，
            # 它运行时本中间件已经退出；保留 rid 让异常处理器仍能拿到它。
            if not failed:
                logs.request_id_var.reset(token)


async def unhandled_error(request: Request, exc: Exception) -> JSONResponse:
    """兜底 500：记录完整堆栈，回给前端可追踪的错误信息。"""
    rid = logs.request_id_var.get()
    log.exception("未处理异常 %s %s", request.method, request.url.path)
    return JSONResponse(
        status_code=500,
        content={"detail": f"服务内部错误，请把请求编号 {rid} 提供给开发者"},
        headers={"X-Request-Id": rid},
    )
