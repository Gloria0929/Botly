"""FastAPI 入口。API 只负责收发消息、鉴权与推送，Agent 循环在 Worker 进程里跑。"""
from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from . import redis_bus
from .commit_middleware import CommitBeforeResponseMiddleware
from .http_logging import RequestLogMiddleware, unhandled_error
from .logging_setup import LOG_DIR, get_logger, setup_logging
from .api import (
    auth,
    bots,
    chats,
    files,
    logs,
    memories,
    metrics,
    notifications,
    providers,
    runs,
    settings,
    stats,
    stream,
    uploads,
    usage,
)
from .db import init_db


@asynccontextmanager
async def lifespan(app: FastAPI):
    setup_logging("api")
    get_logger("main").info("API 启动，日志目录 %s", LOG_DIR)
    init_db()
    yield


app = FastAPI(title="Botly · 多智能体工作台", version="1.0.0", lifespan=lifespan)

# 请求日志放在最外层，记录到的耗时包含 CORS 等后续中间件
app.add_middleware(RequestLogMiddleware)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:3100",
        "http://127.0.0.1:3100",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["*"],
)

# 最后加入即最外层：提交发生在响应字节离开之前，且在日志中间件
# 的读取之后，不影响日志统计与流式响应。
app.add_middleware(CommitBeforeResponseMiddleware)

for r in (
        auth.router,
        providers.router,
        bots.router,
        chats.router,
        runs.router,
        memories.router,
        metrics.router,
        files.router,
        logs.router,
        notifications.router,
        settings.router,
        stats.router,
        stream.router,
        uploads.router,
        usage.router,
):
    app.include_router(r, prefix="/api")


@app.get("/api/health")
def health():
    return {
        "ok": True,
        "redis": redis_bus.available(),
        "queue_depth": redis_bus.queue_depth(),
        "version": "1.0.0",
    }


@app.exception_handler(Exception)
async def unhandled(request, exc):  # pragma: no cover
    return await unhandled_error(request, exc)
