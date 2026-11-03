"""Prometheus 文本格式指标（需求 8.6 可观测性）。

不引入 prometheus_client 依赖，直接手写 text exposition 格式。
指标是「当前状态快照 + 累计计数」，供监控系统抓取和告警：

访问控制（三者满足其一）：
  1. 已登录用户（session cookie），只返回当前用户的指标；
  2. 设置 `BOTLY_METRICS_TOKEN` 后，携带 `Authorization: Bearer <token>`；
  3. 未设置 token 时，仅回环地址（127.0.0.1 / ::1）可访问，便于本机 Prometheus 抓取。
其它情况返回 403。
"""
from __future__ import annotations

import os

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import PlainTextResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import SESSION_COOKIE
from app.core.db import get_db
from app.core.models import Approval, Question, Run, UsageLog, User
from app.core.redis_bus import queue_depth
from app.core.security import read_session

router = APIRouter(prefix="/metrics", tags=["metrics"])

METRICS_TOKEN = os.getenv("BOTLY_METRICS_TOKEN", "")

_ACTIVE = ("queued", "running", "waiting", "waiting_children")


def _scope(request: Request, db: Session) -> str | None:
    auth = request.headers.get("authorization", "")
    if METRICS_TOKEN and auth == f"Bearer {METRICS_TOKEN}":
        return None
    # 1) 已登录用户
    token = request.cookies.get(SESSION_COOKIE)
    if token:
        session = read_session(token)
        if session:
            user = db.get(User, session[0])
            if user and user.status == "active" and session[1] == (user.session_epoch or 0):
                return user.id
    # 3) 回环地址（仅当未配置 token 时开放）
    host = request.client.host if request.client else ""
    forwarded = any(name in request.headers for name in ("x-forwarded-for", "x-forwarded-host", "x-forwarded-proto", "forwarded"))
    if not METRICS_TOKEN and not forwarded and host in ("127.0.0.1", "::1", "localhost"):
        return None
    raise HTTPException(403, "禁止访问指标")


def _gauge(name: str, help_: str, value: float) -> list[str]:
    return [f"# HELP {name} {help_}", f"# TYPE {name} gauge", f"{name} {value}"]


def _counter(name: str, help_: str, value: float) -> list[str]:
    return [f"# HELP {name} {help_}", f"# TYPE {name} counter", f"{name} {value}"]


@router.get("")
def metrics(request: Request, db: Session = Depends(get_db)):
    user_id = _scope(request, db)
    def owned(model):
        return (model.user_id == user_id,) if user_id else ()

    status_rows = dict(
        db.execute(select(Run.status, func.count(Run.id)).where(*owned(Run)).group_by(Run.status)).all()
    )
    active = sum(int(status_rows.get(s, 0)) for s in _ACTIVE)
    approvals_pending = db.scalar(
        select(func.count(Approval.id)).where(*owned(Approval), Approval.status == "pending")
    ) or 0
    questions_pending = db.scalar(
        select(func.count(Question.id)).where(*owned(Question), Question.status == "pending")
    ) or 0
    input_tokens = db.scalar(select(func.coalesce(func.sum(UsageLog.input_tokens), 0)).where(*owned(UsageLog))) or 0
    output_tokens = db.scalar(select(func.coalesce(func.sum(UsageLog.output_tokens), 0)).where(*owned(UsageLog))) or 0
    calls = db.scalar(select(func.count(UsageLog.id)).where(*owned(UsageLog))) or 0
    errors = db.scalar(
        select(func.count(UsageLog.id)).where(*owned(UsageLog), UsageLog.error.isnot(None))
    ) or 0
    users = 1 if user_id else (db.scalar(select(func.count(User.id))) or 0)

    lines: list[str] = []
    lines += _gauge("botly_runs_active", "当前未结束的执行数（queued/running/waiting/waiting_children）", active)
    for status in ("queued", "running", "waiting", "waiting_children", "completed", "failed", "stopped"):
        lines.append(f'botly_runs{{status="{status}"}} {int(status_rows.get(status, 0))}')
    depth = int(status_rows.get("queued", 0)) if user_id else queue_depth()
    lines += _gauge("botly_queue_depth", "待执行的 run 数", depth)
    lines += _gauge("botly_approvals_pending", "待处理的审批数", approvals_pending)
    lines += _gauge("botly_questions_pending", "待回复的提问数", questions_pending)
    lines += _gauge("botly_users_total", "注册用户数", users)
    lines += _counter("botly_usage_calls_total", "累计模型调用次数", calls)
    lines += _counter("botly_usage_errors_total", "累计模型调用失败次数", errors)
    lines.append("# HELP botly_usage_tokens_total 累计 token（input/output）")
    lines.append("# TYPE botly_usage_tokens_total counter")
    lines.append(f'botly_usage_tokens_total{{kind="input"}} {int(input_tokens)}')
    lines.append(f'botly_usage_tokens_total{{kind="output"}} {int(output_tokens)}')
    return PlainTextResponse("\n".join(lines) + "\n", media_type="text/plain; version=0.0.4; charset=utf-8")
