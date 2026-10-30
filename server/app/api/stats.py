"""8.6 运行状况看板：执行完成率、失败原因分布、告警与队列。

统计按当前登录用户隔离（只能看自己的执行）；告警是部署级、body 不含用户隐私。
"""
from __future__ import annotations

from collections import Counter
from datetime import timedelta

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..db import get_db
from ..deps import current_user
from ..models import Alert, Chat, Run, User, now
from ..redis_bus import queue_depth

router = APIRouter(prefix="/stats", tags=["stats"])


def _reason_bucket(reason: str) -> str:
    """把自由文本的失败原因归一成可聚合的短类别。"""
    text = (reason or "未知原因").strip()
    for sep in ("：", ":"):
        if sep in text:
            head = text.split(sep, 1)[0].strip()
            if head:
                return head[:40]
    return text[:60]


@router.get("/overview")
def overview(
    days: int = Query(default=7, ge=1, le=90),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    today = now().replace(hour=0, minute=0, second=0, microsecond=0)
    since = today - timedelta(days=days - 1)
    runs = list(
        db.scalars(
            select(Run)
            .where(Run.user_id == user.id, Run.created_at >= since)
            .order_by(Run.created_at)
        ).all()
    )
    status_count = Counter(r.status for r in runs)
    finished = [r for r in runs if r.status in ("completed", "failed", "stopped")]
    completed = status_count.get("completed", 0)
    success_rate = completed / len(finished) if finished else 0.0
    durations = [
        (r.ended_at - r.started_at).total_seconds()
        for r in finished
        if r.started_at and r.ended_at
    ]
    avg_duration = sum(durations) / len(durations) if durations else 0.0
    avg_steps = sum(r.steps_used for r in runs) / len(runs) if runs else 0.0
    avg_tokens = sum(r.tokens_used for r in runs) / len(runs) if runs else 0.0

    daily_map: dict[str, dict] = {}
    for r in runs:
        day = r.created_at.strftime("%Y-%m-%d")
        slot = daily_map.setdefault(
            day, {"date": day, "completed": 0, "failed": 0, "stopped": 0, "other": 0}
        )
        if r.status == "completed":
            slot["completed"] += 1
        elif r.status == "failed":
            slot["failed"] += 1
        elif r.status == "stopped":
            slot["stopped"] += 1
        else:
            slot["other"] += 1
    daily = []
    for i in range(days - 1, -1, -1):
        d = (today - timedelta(days=i)).strftime("%Y-%m-%d")
        daily.append(
            daily_map.get(d)
            or {"date": d, "completed": 0, "failed": 0, "stopped": 0, "other": 0}
        )

    counter = Counter(_reason_bucket(r.end_reason) for r in runs if r.status == "failed")
    failures = [{"reason": k, "count": v} for k, v in counter.most_common(8)]

    failed = [r for r in runs if r.status == "failed"]
    chat_ids = {r.chat_id for r in failed}
    chats = (
        {c.id: c.name for c in db.scalars(select(Chat).where(Chat.id.in_(chat_ids))).all()}
        if chat_ids
        else {}
    )
    recent_failures = [
        {
            "run_id": r.id,
            "chat_id": r.chat_id,
            "chat_name": chats.get(r.chat_id, "（已删除群聊）"),
            "reason": r.end_reason or "",
            "ended_at": r.ended_at.isoformat() if r.ended_at else None,
        }
        for r in sorted(failed, key=lambda x: x.ended_at or x.created_at, reverse=True)[:8]
    ]

    alerts = db.scalars(
        select(Alert).order_by(Alert.created_at.desc()).limit(10)
    ).all()
    queued_now = db.scalar(
        select(func.count(Run.id)).where(Run.user_id == user.id, Run.status == "queued")
    ) or 0

    return {
        "days": days,
        "totals": {
            "runs": len(runs),
            "completed": completed,
            "failed": status_count.get("failed", 0),
            "stopped": status_count.get("stopped", 0),
            "running": status_count.get("running", 0),
            "queued": int(queued_now),
            "success_rate": round(success_rate, 4),
            "avg_duration_sec": round(avg_duration, 1),
            "avg_steps": round(avg_steps, 1),
            "avg_tokens": round(avg_tokens, 1),
        },
        "daily": daily,
        "failures": failures,
        "recent_failures": recent_failures,
        "queue": {"depth": queue_depth(), "queued": int(queued_now)},
        "alerts": [
            {
                "id": a.id,
                "kind": a.kind,
                "level": a.level,
                "title": a.title,
                "body": a.body,
                "created_at": a.created_at.isoformat() if a.created_at else None,
                "acknowledged": bool(a.acknowledged_at),
            }
            for a in alerts
        ],
    }
