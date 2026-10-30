"""账号删除与彻底清除（ACC-07）。

删除账号分两步，避免误删后无法挽回：

1. **停用**（`DELETE /api/account`）：账号状态改为 `deleted`，所有接口立刻返回 401；
   未完成的执行标记为已停止。业务数据保留 30 天，期间管理员可用
   `scripts/purge_accounts.py --restore <邮箱>` 恢复。
2. **彻底清除**：到期后由 Worker 的小时级清理自动执行，也可以用
   `scripts/purge_accounts.py --purge <邮箱>` 立即执行。清除范围包括全部业务表、
   沙箱文件、上传图片、登录失败记录与加密后的 Key。

保留期内的数据对用户不可见（登录与接口都被拒绝），只对持有服务器权限的管理员可见。
"""
from __future__ import annotations

import shutil
from datetime import timedelta

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from .config import DATA_DIR, SANDBOX_DIR
from .models import (
    Approval,
    Bot,
    Chat,
    ChatMember,
    Event,
    FileRec,
    LoginAttempt,
    Memory,
    Message,
    Notification,
    Provider,
    Question,
    Run,
    Task,
    Thread,
    Upload,
    UsageLog,
    User,
    UserSettings,
    now,
)

RETENTION_DAYS = 30
ACTIVE_RUN_STATUSES = ("queued", "running", "waiting")
OPEN_TASK_STATUSES = ("queued", "running", "waiting_approval", "waiting_reply")

# 彻底清除的顺序：先子表后主表，满足外键约束。
_PURGE_ORDER = (
    Event,
    UsageLog,
    Approval,
    Question,
    Message,
    Task,
    Run,
    FileRec,
    Thread,
    ChatMember,
    Chat,
    Memory,
    Notification,
    Upload,
    Provider,
    Bot,
    UserSettings,
)


def disable_user(db: Session, user: User) -> None:
    """停用账号并终止未完成的执行；数据保留到 `purge_after`。"""
    stamp = now()
    user.status = "deleted"
    user.deleted_at = stamp
    user.purge_after = stamp + timedelta(days=RETENTION_DAYS)

    for run in db.scalars(
        select(Run).where(Run.user_id == user.id, Run.status.in_(ACTIVE_RUN_STATUSES))
    ).all():
        run.status = "stopped"
        run.stop_requested = True
        run.ended_at = stamp
        run.end_reason = "账号已删除，执行终止"
        run.resume_payload = None

    for task in db.scalars(
        select(Task).where(Task.user_id == user.id, Task.status.in_(OPEN_TASK_STATUSES))
    ).all():
        task.status = "canceled"
        task.ended_at = stamp

    for approval in db.scalars(
        select(Approval).where(Approval.user_id == user.id, Approval.status == "pending")
    ).all():
        approval.status = "invalidated"
        approval.resolved_at = stamp
        approval.reject_reason = "账号已删除"

    for question in db.scalars(
        select(Question).where(Question.user_id == user.id, Question.status == "pending")
    ).all():
        question.status = "expired"

    db.flush()


def purge_user(db: Session, user: User) -> dict[str, int]:
    """删除该用户的全部数据与磁盘文件，返回各表删除行数。"""
    user_id, email = user.id, user.email
    counts: dict[str, int] = {}
    for model in _PURGE_ORDER:
        result = db.execute(delete(model).where(model.user_id == user_id))
        counts[model.__tablename__] = int(result.rowcount or 0)
    removed = db.execute(delete(LoginAttempt).where(LoginAttempt.email == email))
    counts["login_attempts"] = int(removed.rowcount or 0)
    db.delete(user)
    db.flush()

    shutil.rmtree(SANDBOX_DIR / user_id, ignore_errors=True)
    shutil.rmtree(DATA_DIR / "uploads" / user_id, ignore_errors=True)
    return counts


def purge_due_accounts(db: Session) -> list[str]:
    """清除超过保留期的账号，返回被清除的邮箱（供日志）。"""
    due = db.scalars(
        select(User).where(
            User.status == "deleted",
            User.purge_after.isnot(None),
            User.purge_after <= now(),
        )
    ).all()
    emails = [u.email for u in due]
    for user in due:
        purge_user(db, user)
    return emails
