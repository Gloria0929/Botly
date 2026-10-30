"""响应发出前提交中间件（纯 ASGI，不缓冲、不重建响应，不破坏 SSE）。

背景：get_db 是 yield 依赖，它的提交发生在响应发出之后；客户端紧跟着
发来的下一个请求可能读不到上一个请求刚写的数据（实测：注册后立刻登录
401、建厂商后立刻绑 Bot 404、PATCH 后立刻读回旧值）。

本中间件在 http.response.start 到达时——即响应字节真正发出之前——提交
本请求的会话，让提交先于响应到达客户端。

只提交、不关闭：流式响应（SSE）的生成器可能在发送过程中继续读会话，
会话仍由 get_db 在流结束后的清理里关闭；且 SessionLocal 用的是
expire_on_commit=False，提交不会使已加载属性过期。端点抛异常时，
get_db 的回滚已在异常处理中先行执行，此处提交是空操作，不会把失败
的写入扶正；提交本身失败时回滚并记日志，不阻断响应（与今天提交失败
时的客户端可见行为一致）。
"""

from __future__ import annotations

from .logging_setup import get_logger

log = get_logger("http")


class CommitBeforeResponseMiddleware:
    """在响应发出前提交本请求数据库会话。"""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        committed = False

        async def send_with_commit(message):
            nonlocal committed
            if message["type"] == "http.response.start" and not committed:
                committed = True
                db = scope.get("state", {}).get("db_session")
                if db is not None:
                    try:
                        db.commit()
                    except Exception:
                        db.rollback()
                        log.exception("响应前提交失败，已回滚")
            await send(message)

        await self.app(scope, receive, send_with_commit)
