"""8.6 告警：队列积压、执行卡死、失败率。

Worker 每分钟调用一次 `check_alerts`。告警写 `alerts` 表（系统级，不含用户隐私），
运行状况看板读取；同类告警在冷却期内不重复写，避免刷屏。
"""
from __future__ import annotations

from datetime import timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .config import (
    ALERT_COOLDOWN_MINUTES,
    ALERT_FAIL_MIN_RUNS,
    ALERT_FAIL_RATE,
    ALERT_FAIL_WINDOW_MINUTES,
    ALERT_QUEUE_BACKLOG,
    ALERT_RUN_STUCK_MINUTES,
)
from .logging_setup import get_logger
from .models import Alert, Run, now
from .redis_bus import queue_depth

log = get_logger("alerts")


def _has_recent(db: Session, kind: str) -> bool:
    cutoff = now() - timedelta(minutes=ALERT_COOLDOWN_MINUTES)
    return bool(
        db.scalar(
            select(func.count(Alert.id)).where(
                Alert.kind == kind, Alert.created_at >= cutoff
            )
        )
    )


def _raise(
    db: Session, *, kind: str, level: str, title: str, body: str
) -> Alert | None:
    if _has_recent(db, kind):
        return None
    alert = Alert(kind=kind, level=level, title=title, body=body)
    db.add(alert)
    db.flush()
    log.warning("告警 kind=%s level=%s %s", kind, level, title)
    return alert


def check_alerts(db: Session) -> list[Alert]:
    """检查一次告警条件，返回本次新写入的告警。"""
    fired: list[Alert] = []

    # 1) 队列积压：数据库 queued 与 Redis 队列取较大值
    queued = db.scalar(select(func.count(Run.id)).where(Run.status == "queued")) or 0
    depth = queue_depth()
    backlog = max(int(queued), int(depth))
    if backlog >= ALERT_QUEUE_BACKLOG:
        alert = _raise(
            db,
            kind="queue_backlog",
            level="warning",
            title=f"执行队列积压 {backlog} 个",
            body=f"数据库排队 {queued} 个，Redis 队列 {depth} 个，阈值 {ALERT_QUEUE_BACKLOG}。请检查 Worker 是否在运行。",
        )
        if alert:
            fired.append(alert)

    # 2) 执行卡死：running 超过阈值仍未结束
    cutoff = now() - timedelta(minutes=ALERT_RUN_STUCK_MINUTES)
    stuck = db.scalars(
        select(Run).where(
            Run.status == "running",
            Run.started_at.isnot(None),
            Run.started_at < cutoff,
        )
    ).all()
    if stuck:
        alert = _raise(
            db,
            kind="run_stuck",
            level="critical",
            title=f"{len(stuck)} 个执行运行超过 {ALERT_RUN_STUCK_MINUTES} 分钟",
            body="疑似卡死：" + "、".join(r.id[:8] for r in stuck[:5]),
        )
        if alert:
            fired.append(alert)

    # 3) 失败率：窗口内失败占比过高
    window = now() - timedelta(minutes=ALERT_FAIL_WINDOW_MINUTES)
    rows = dict(
        db.execute(
            select(Run.status, func.count(Run.id))
            .where(Run.ended_at.isnot(None), Run.ended_at >= window)
            .group_by(Run.status)
        ).all()
    )
    total = sum(int(v) for v in rows.values())
    failed = int(rows.get("failed", 0))
    if total >= ALERT_FAIL_MIN_RUNS and failed / total >= ALERT_FAIL_RATE:
        alert = _raise(
            db,
            kind="fail_rate",
            level="warning",
            title=f"近 {ALERT_FAIL_WINDOW_MINUTES} 分钟失败率 {failed / total:.0%}",
            body=f"{failed}/{total} 次执行失败，阈值 {ALERT_FAIL_RATE:.0%}。",
        )
        if alert:
            fired.append(alert)

    return fired
