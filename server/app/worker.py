"""独立 Worker：从 Redis 队列取执行并状态 Agent 循环。

与 API 进程分离，关闭浏览器不影响执行；进程重启后，
DB 里 status=running 且租约过期的执行会被重新入队，从断点继续。
"""
from __future__ import annotations

import asyncio
import signal
import sys
import uuid
import time
from datetime import timedelta

from sqlalchemy import delete, func, select, text, update
from sqlalchemy.orm import aliased
from app.core import redis_bus
from app.services.alerts import check_alerts
from app.services.approval_email import deliver_pending
from app.sandbox import terminal
from app.core.config import (
    ALERT_CHECK_INTERVAL,
    MAX_CONCURRENT_RUNS,
    MAX_CONCURRENT_RUNS_PER_USER,
    WORKER_CONCURRENCY,
    WORKER_LEASE_SECONDS,
    WORKER_SHUTDOWN_TIMEOUT,
)
from app.core.db import IS_SQLITE, init_db, session_scope
from app.services.maintenance import expire_overdue, prune_expired
from app.core.logging_setup import get_logger, run_id_var, setup_logging
from app.core.models import Approval, LoginAttempt, Run, now
from app.agent.orchestrator import RunExecutor, emit_run_status, execute_run

log = get_logger("worker")

RECONCILE_INTERVAL = 5.0
FREE_CHAT_RECONCILE_INTERVAL = 0.25
PRUNE_INTERVAL = 3600.0
_stop = False
_last_prune = 0.0
_last_alert_check = 0.0
_owned_runs: dict[str, tuple[str, int]] = {}


def _handle_stop(*_args):
    global _stop
    _stop = True


async def _shutdown(running, terminal_tasks, mail_task) -> None:
    """执行、终端、邮件共用退出预算，取消后留出时间落盘并归还执行租约。"""
    tasks = set(running.values()) | set(terminal_tasks.values())
    if mail_task is not None:
        tasks.add(mail_task)
    if not tasks:
        return
    deadline = time.monotonic() + WORKER_SHUTDOWN_TIMEOUT
    cleanup = min(1.0, WORKER_SHUTDOWN_TIMEOUT / 4)
    _, pending = await asyncio.wait(tasks, timeout=max(0.0, WORKER_SHUTDOWN_TIMEOUT - cleanup))
    owners = {rid: _owned_runs[rid] for rid, task in running.items()
              if task in pending and rid in _owned_runs}
    for task in pending:
        task.cancel()
    if pending:
        _, remaining = await asyncio.wait(pending, timeout=max(0.0, deadline - time.monotonic()))
        if remaining:
            log.warning("退出清理超时，仍有 %d 个后台任务等待取消", len(remaining))
    await asyncio.gather(*(task for task in tasks if task.done()), return_exceptions=True)
    if owners:
        await asyncio.to_thread(_requeue_owned_runs, owners)


def _run_prune() -> None:
    with session_scope() as db:
        prune_expired(db)


def _run_alert_check() -> None:
    with session_scope() as db:
        check_alerts(db)


def _requeue_owned_runs(owners: dict[str, tuple[str, int]]) -> None:
    if not owners:
        return
    with session_scope() as db:
        for run_id, (worker_id, lease_token) in owners.items():
            run = db.scalar(select(Run).where(
                Run.id == run_id, Run.status == "running", Run.worker_id == worker_id,
                Run.lease_token == lease_token,
            ))
            if not run:
                continue
            run.status = "queued"
            run.lease_until = None
            run.worker_id = None
            emit_run_status(db, run)


def _wake_waiting_parents(free_only: bool = False) -> list[str]:
    """RUN-12：并行子执行全部结束后，把 waiting_children 的父执行重新排队。

    子执行结束时也会主动唤醒父执行，但进程崩溃/停机可能错过那一次；这里是
    兜底，保证父执行不会永远停在等待状态（最多晚一个 reconcile 周期）。
    """
    woke: list[str] = []
    with session_scope() as db:
        query = select(Run).where(Run.status == "waiting_children")
        if free_only:
            query = query.where(Run.kind == "free", Run.parent_run_id.is_(None))
        parents = db.scalars(query).all()
        for parent in parents:
            active = db.scalar(
                select(func.count(Run.id)).where(
                    Run.parent_run_id == parent.id,
                    Run.status.in_(("queued", "running", "waiting")),
                )
            )
            if active and not (parent.kind == "free" and RunExecutor(parent.id)._free_ready(db, parent)):
                continue
            parent.status = "queued"
            db.flush()
            emit_run_status(db, parent)
            woke.append(parent.id)
    return woke


def _recover_and_collect() -> list[str]:
    """恢复僵尸执行 + 收集可入队的 queued 执行。"""
    picked: list[str] = []
    with session_scope() as db:
        expire_overdue(db)
        db.execute(delete(LoginAttempt).where(LoginAttempt.created_at < now() - timedelta(hours=1)))
        stale = db.scalars(
            select(Run).where(
                Run.status == "running",
                Run.lease_until.isnot(None),
                Run.lease_until < now(),
            )
        ).all()
        for r in stale:
            recovered = db.execute(update(Run).where(
                Run.id == r.id, Run.status == "running", Run.lease_until < now(),
                ).values(status="queued", lease_until=None, worker_id=None))
            if recovered.rowcount:
                log.info("回收僵尸执行 run=%s", r.id)

        # 自愈：审批竞态残留。正常路径下批准会把执行原子地置为 queued，
        # 因此“waiting + 对应审批已批准且未被消费”只可能是暂停落盘覆盖了
        # 批准的残留（或批准后进程崩溃）。先于 queued 扫描前处理，让本轮
        # 就能直接认领；不会误伤正常等待（那时审批还是 pending）。
        stuck = db.scalars(
            select(Run).where(Run.status == "waiting").order_by(Run.created_at).limit(50)
        ).all()
        for r in stuck:
            payload = r.resume_payload or {}
            if payload.get("kind") != "approval" or not payload.get("approval_id"):
                continue
            ap = db.get(Approval, payload["approval_id"])
            if ap and ap.status == "approved" and ap.consumed_at is None:
                healed = db.execute(update(Run).where(
                    Run.id == r.id, Run.status == "waiting",
                ).values(status="queued"))
                if healed.rowcount:
                    log.info("自愈卡住的审批执行 run=%s approval=%s", r.id, ap.id)

        active_user_run = aliased(Run)
        user_running = select(func.count(active_user_run.id)).where(
            active_user_run.user_id == Run.user_id,
            active_user_run.status == "running",
            active_user_run.parent_run_id.is_(None),
            active_user_run.kind.not_in(("chime", "free")),
        ).correlate(Run).scalar_subquery()
        queued = db.scalars(
            select(Run).where(
                Run.status == "queued",
                (Run.kind.in_(("chime", "free"))) | (Run.parent_run_id.isnot(None)) | (
                    user_running < MAX_CONCURRENT_RUNS_PER_USER
                ),
            ).order_by(Run.kind != "chime", Run.created_at).limit(50)
        ).all()
        for r in queued:
            if r.parent_run_id:
                # 子执行不受「每用户并发数」限制：一次并行派任务本来就会有多个
                # 子任务同时跑，按顶层执行的上限卡住会让批次永远起不来。
                picked.append(r.id)
                continue
            picked.append(r.id)
    return picked


def _claim(run_id: str, worker_id: str) -> int:
    """容量检查与原子认领共用数据库锁，多 Worker 不会重复认领或超出并发上限。"""
    with session_scope() as db:
        # 各 Worker 的容量检查和认领放在同一把数据库锁内，避免同时越过配额。
        if IS_SQLITE:
            db.connection().exec_driver_sql("BEGIN IMMEDIATE")
        else:
            db.execute(text("SELECT pg_advisory_xact_lock(782341991)"))
        run = db.get(Run, run_id)
        if not run or run.status != "queued":
            return False
        from app.agent.free_chat import free_run_blocked
        if free_run_blocked(db, run):
            return False
        total = db.scalar(select(func.count(Run.id)).where(
            Run.status == "running",
        )) or 0
        if total >= MAX_CONCURRENT_RUNS:
            return False
        # 子执行只受全局并发限制，不再占「每用户并发」的名额（RUN-12）
        if not run.parent_run_id and run.kind not in ("free", "chime"):
            personal = db.scalar(
                select(func.count(Run.id)).where(
                    Run.status == "running",
                    Run.user_id == run.user_id,
                    Run.parent_run_id.is_(None),
                    Run.kind.not_in(("chime", "free")),
                )
            ) or 0
            if personal >= MAX_CONCURRENT_RUNS_PER_USER:
                return False
        lease_token = (run.lease_token or 0) + 1
        res = db.execute(
            update(Run)
            .where(Run.id == run_id, Run.status == "queued")
            .values(
                status="running",
                lease_until=now() + timedelta(seconds=WORKER_LEASE_SECONDS),
                worker_id=worker_id,
                lease_token=lease_token,
            )
        )
        return lease_token if res.rowcount == 1 else 0


def _extend_lease(run_id: str, worker_id: str, lease_token: int) -> bool:
    with session_scope() as db:
        result = db.execute(update(Run).where(
            Run.id == run_id, Run.status == "running", Run.worker_id == worker_id,
            Run.lease_token == lease_token,
            Run.lease_until > now(),
        ).values(lease_until=now() + timedelta(seconds=WORKER_LEASE_SECONDS)))
        return result.rowcount == 1


def _pending_free_runs() -> list[str]:
    with session_scope() as db:
        return list(db.scalars(select(Run.id).where(Run.status == "queued", Run.kind.in_(("free", "chime")))
                               .order_by(Run.created_at).limit(50)))


async def _heartbeat(run_id: str, worker_id: str, lease_token: int, lost: asyncio.Event) -> None:
    failures = 0
    while True:
        await asyncio.sleep(max(10, WORKER_LEASE_SECONDS // 3))
        try:
            owned = await asyncio.to_thread(_extend_lease, run_id, worker_id, lease_token)
            failures = 0
            if not owned:
                lost.set()
                return
        except Exception:  # noqa: BLE001
            failures += 1
            log.exception("执行租约续期失败 run=%s attempt=%d", run_id, failures)
            if failures >= 2:
                lost.set()
                return


async def process_one(run_id: str) -> None:
    worker_id = uuid.uuid4().hex
    lease_token = await asyncio.to_thread(_claim, run_id, worker_id)
    if not lease_token:
        log.debug("认领跳过 run=%s（已被其他 Worker 或状态不允许）", run_id)
        return
    _owned_runs[run_id] = (worker_id, lease_token)
    run_id_var.set(run_id)
    log.info("开始执行 run=%s", run_id)
    lost = asyncio.Event()
    hb = asyncio.create_task(_heartbeat(run_id, worker_id, lease_token, lost))
    execution = asyncio.create_task(execute_run(run_id, worker_id, lease_token))
    try:
        done, _ = await asyncio.wait((execution, hb), return_when=asyncio.FIRST_COMPLETED)
        if hb in done and lost.is_set():
            execution.cancel()
            await asyncio.gather(execution, return_exceptions=True)
            log.error("执行租约丢失，已中止本地执行 run=%s", run_id)
            return
        await execution
        log.info("执行结束 run=%s", run_id)
    except Exception as exc:
        log.exception("执行异常 run=%s: %s", run_id, type(exc).__name__)
        await asyncio.to_thread(
            RunExecutor(run_id, worker_id, lease_token)._finish_failed,
            f"执行初始化异常：{type(exc).__name__}",
        )
    finally:
        hb.cancel()
        execution.cancel() if not execution.done() else None
        await asyncio.gather(hb, execution, return_exceptions=True)
        _owned_runs.pop(run_id, None)
        run_id_var.set("-")


async def main(once: bool = False) -> None:
    global _last_prune, _last_alert_check
    setup_logging("worker")
    init_db()
    signal.signal(signal.SIGTERM, _handle_stop)
    signal.signal(signal.SIGINT, _handle_stop)
    log.info(
        "Worker 启动，redis=%s，并发上限=%d",
        "ok" if redis_bus.available() else "unavailable（降级为 DB 轮询）",
        WORKER_CONCURRENCY,
    )
    last_reconcile = 0.0
    last_free_reconcile = 0.0
    if once:
        await asyncio.to_thread(_wake_waiting_parents)
        run_id = await redis_bus.dequeue(timeout=2)
        pending = [run_id] if run_id else await asyncio.to_thread(_recover_and_collect)
        if pending:
            await process_one(pending[0])
        return

    running: dict[str, asyncio.Task] = {}
    terminal_tasks: dict[str, asyncio.Task] = {}
    mail_task: asyncio.Task | None = None

    async def schedule_pending(pending_runs):
        for rid in pending_runs:
            if rid in running:
                continue
            if len(running) >= WORKER_CONCURRENCY:
                continue
            running[rid] = asyncio.create_task(process_one(rid))
    try:
        while not _stop:
            for cid, task in list(terminal_tasks.items()):
                if task.done():
                    terminal_tasks.pop(cid)
                    try:
                        task.result()
                    except Exception:
                        log.exception("用户终端命令失败 command=%s", cid)
            for rid, task in list(running.items()):
                if task.done():
                    running.pop(rid)
                    try:
                        task.result()
                    except Exception as exc:
                        log.exception("执行异常 run=%s: %s", rid, type(exc).__name__)
            # 清理任务与过期交互不再被一次长执行阻塞。
            if time.monotonic() - last_free_reconcile >= FREE_CHAT_RECONCILE_INTERVAL:
                last_free_reconcile = time.monotonic()
                for rid in await asyncio.to_thread(_wake_waiting_parents, True):
                    await asyncio.to_thread(redis_bus.enqueue, rid)
                await schedule_pending(await asyncio.to_thread(_pending_free_runs))
            if time.time() - last_reconcile > RECONCILE_INTERVAL:
                last_reconcile = time.time()
                # 兼容旧版本遗留的 queued 手动命令；新命令由 API 直接执行。
                for cid in await asyncio.to_thread(terminal.pending):
                    if cid not in terminal_tasks and len(terminal_tasks) < 2:
                        terminal_tasks[cid] = asyncio.create_task(terminal.execute(cid, claim=True))
                if mail_task is None or mail_task.done():
                    if mail_task is not None:
                        try:
                            mail_task.result()
                        except Exception:
                            log.exception("审批邮件队列处理失败")
                    mail_task = asyncio.create_task(asyncio.to_thread(deliver_pending))
                # 事件与调用日志的过期清理按小时执行，避免每轮扫描整表。
                if time.time() - _last_prune > PRUNE_INTERVAL:
                    _last_prune = time.time()
                    await asyncio.to_thread(_run_prune)
                # 8.6 告警：队列积压 / 执行卡死 / 失败率，默认每分钟一次
                if time.time() - _last_alert_check > ALERT_CHECK_INTERVAL:
                    _last_alert_check = time.time()
                    await asyncio.to_thread(_run_alert_check)
                # 直接认领数据库里的 queued 执行，而不是往 Redis 再塞一份。
                # 之前每 5 秒重复 LPUSH 会让队列无限膨胀，Redis 断开时
                # 还依赖出队失败才走数据库路径；_claim 本身是原子的，直接跑更稳。
                # RUN-12：先唤醒「子任务都跑完」的父执行，否则它会一直等下去。
                await asyncio.to_thread(_wake_waiting_parents)
                pending_runs = await asyncio.to_thread(_recover_and_collect)
                await schedule_pending(pending_runs)
            if len(running) >= WORKER_CONCURRENCY:
                await asyncio.wait(list(running.values()), timeout=0.5, return_when=asyncio.FIRST_COMPLETED)
                continue
            run_id = await redis_bus.dequeue(timeout=1)
            if run_id and run_id not in running:
                if len(running) < WORKER_CONCURRENCY:
                    running[run_id] = asyncio.create_task(process_one(run_id))
            await asyncio.sleep(0.1)
    finally:
        await _shutdown(running, terminal_tasks, mail_task)
    log.info("Worker 退出")


if __name__ == "__main__":
    asyncio.run(main(once="--once" in sys.argv))
