"""执行中的超时清理：审批过期、提问过期。

这些状态不会自己消失：执行停在「等待用户」后不占用 Worker，
如果用户一直不处理，run 会永远挂在 waiting。API 侧只在有人打开审批列表时
顺带检查，所以必须有独立的后台清扫（Worker 的 reconcile 循环调用）。
"""
from __future__ import annotations

from datetime import timedelta

from sqlalchemy import delete, or_, select, update
from sqlalchemy.orm import Session

from .config import EVENT_RETENTION_DAYS, LOG_RETENTION_DAYS
from .accounts import purge_due_accounts
from .events import emit, notify
from .models import Approval, Event, Message, Question, Run, UsageLog, now
from .logging_setup import get_logger
from .orchestrator import emit_run_status, settle_run_tasks

log = get_logger("maintenance")


def _patch_card(
    db: Session, run_id: str, type_: str, key: str, value: str, patch: dict
) -> None:
    """把审批/提问卡片的 meta 状态改掉，避免前端还显示可点的按钮。"""
    for msg in db.scalars(
        select(Message).where(Message.run_id == run_id, Message.type == type_)
    ).all():
        if (msg.meta or {}).get(key) == value:
            msg.meta = {**(msg.meta or {}), **patch}
            db.flush()
            return


def _fail_waiting_run(db: Session, run: Run, reason: str) -> None:
    """只有停在等待中的执行才因为超时失败；已经结束的不要覆盖。"""
    if run.status != "waiting":
        return
    run.status = "failed"
    run.ended_at = now()
    run.end_reason = reason
    run.resume_payload = None
    settle_run_tasks(db, run, "failed", reason)
    db.flush()
    emit_run_status(db, run)


def expire_approval(db: Session, ap: Approval) -> bool:
    """单个审批过期。已经处理过的返回 False。"""
    if ap.status != "pending" or not ap.expires_at or ap.expires_at >= now():
        return False
    ap.status = "expired"
    ap.resolved_at = now()
    db.flush()
    _patch_card(db, ap.run_id, "approval", "approval_id", ap.id, {"status": "expired"})
    emit(
        db,
        ap.user_id,
        db.get(Run, ap.run_id).chat_id if db.get(Run, ap.run_id) else None,
        "approval.resolved",
        {"approval_id": ap.id, "run_id": ap.run_id, "status": "expired"},
    )
    run = db.get(Run, ap.run_id)
    reason = "审批过期，用户未在规定时间内处理"
    if run:
        _fail_waiting_run(db, run, reason)
        notify(
            db,
            ap.user_id,
            "approval_expired",
            "审批已过期",
            f"{ap.tool} 的审批请求已过期，执行已终止",
            chat_id=run.chat_id,
            target_type="approval",
            target_id=ap.id,
        )
    return True


def expire_question(db: Session, q: Question) -> bool:
    if q.status != "pending" or not q.expires_at or q.expires_at >= now():
        return False
    q.status = "expired"
    db.flush()
    _patch_card(db, q.run_id, "question", "question_id", q.id, {"status": "expired"})
    run = db.get(Run, q.run_id)
    reason = "提问过期，用户未在规定时间内回复"
    if run:
        _fail_waiting_run(db, run, reason)
        notify(
            db,
            q.user_id,
            "question_expired",
            "提问已过期",
            f"{q.question[:80]} —— 执行已终止",
            chat_id=run.chat_id,
            target_type="question",
            target_id=q.id,
        )
    return True


def expire_overdue(db: Session) -> int:
    """扫描所有过期未处理的审批与提问，返回处理数量。"""
    count = 0
    for ap in db.scalars(
        select(Approval).where(
            Approval.status == "pending",
            Approval.expires_at.isnot(None),
            Approval.expires_at < now(),
        )
    ).all():
        count += 1 if expire_approval(db, ap) else 0
    for q in db.scalars(
        select(Question).where(
            Question.status == "pending",
            Question.expires_at.isnot(None),
            Question.expires_at < now(),
        )
    ).all():
        count += 1 if expire_question(db, q) else 0
    return count


def prune_expired(db: Session) -> dict[str, int]:
    """清理过期数据（需求 8.5、第 10 节）。

    - events 只保留 7 天，用于 SSE 的 Last-Event-ID 补发，再久就没有意义。
    - 调用日志保留 30 天；到期后只清掉错误详情与耗时，token
      统计继续保留，用量页仍能按历史聚合。
    - 已删除账号超过 30 天保留期后彻底清除（ACC-07）。
    """
    removed_events = db.execute(
        delete(Event).where(Event.created_at < now() - timedelta(days=EVENT_RETENTION_DAYS))
    ).rowcount or 0
    scrubbed_logs = db.execute(
        update(UsageLog)
        .where(
            UsageLog.created_at < now() - timedelta(days=LOG_RETENTION_DAYS),
            or_(UsageLog.error.isnot(None), UsageLog.latency_ms != 0),
        )
        .values(error=None, latency_ms=0)
    ).rowcount or 0
    purged_accounts = purge_due_accounts(db)
    if purged_accounts:
        log.warning("已彻底清除到期账号 %d 个", len(purged_accounts))
    return {
        "events": removed_events,
        "usage_details": scrubbed_logs,
        "accounts": len(purged_accounts),
    }
