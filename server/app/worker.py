"""独立 Worker：从 Redis 队列取执行并运行 Agent 循环。

与 API 进程分离，关闭浏览器不影响执行；进程重启后，
DB 里 status=running 且租约过期的执行会被重新入队，从断点继续。
"""
from __future__ import annotations

import asyncio
import signal
import sys
from datetime import timedelta

from sqlalchemy import delete, func, select, text, update
from . import redis_bus
from .alerts import check_alerts
from .config import (
    ALERT_CHECK_INTERVAL,
    MAX_CONCURRENT_RUNS,
    MAX_CONCURRENT_RUNS_PER_USER,
    WORKER_CONCURRENCY,
    WORKER_LEASE_SECONDS,
)
from .db import IS_SQLITE, init_db, session_scope
from .maintenance import expire_overdue, prune_expired
from .logging_setup import get_logger, run_id_var, setup_logging
from .models import LoginAttempt, Run, now
from .orchestrator import RunExecutor, execute_run

log = get_logger("worker")

RECONCILE_INTERVAL = 5.0
PRUNE_INTERVAL = 3600.0
_stop = False
_last_prune = 0.0
_last_alert_check = 0.0


def _handle_stop(*_args):
    global _stop
    _stop = True


def _recover_and_collect() -> list[str]:
    """恢复僵尸执行 + 收集可入队的 queued 执行。"""
    picked: list[str] = []
    with session_scope() as db:
        expire_overdue(db)
        db.execute(delete(LoginAttempt).where(LoginAttempt.created_at < now() - timedelta(minutes=15)))
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
            ).values(status="queued", lease_until=None))
            if recovered.rowcount:
                log.info("回收僵尸执行 run=%s", r.id)

        queued = db.scalars(
            select(Run).where(Run.status == "queued").order_by(Run.created_at).limit(50)
        ).all()
        for r in queued:
            running = db.scalar(
                select(func.count(Run.id)).where(
                    Run.user_id == r.user_id, Run.status == "running"
                )
            )
            if (running or 0) >= MAX_CONCURRENT_RUNS_PER_USER:
                continue
            picked.append(r.id)
    return picked


def _claim(run_id: str) -> bool:
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
        total = db.scalar(select(func.count(Run.id)).where(Run.status == "running")) or 0
        personal = db.scalar(select(func.count(Run.id)).where(Run.status == "running", Run.user_id == run.user_id)) or 0
        if total >= MAX_CONCURRENT_RUNS or personal >= MAX_CONCURRENT_RUNS_PER_USER:
            return False
        res = db.execute(
            update(Run)
            .where(Run.id == run_id, Run.status == "queued")
            .values(
                status="running",
                lease_until=now() + timedelta(seconds=WORKER_LEASE_SECONDS),
            )
        )
        return res.rowcount == 1


async def _extend_lease(run_id: str) -> None:
    with session_scope() as db:
        run = db.get(Run, run_id)
        if run and run.status == "running":
            run.lease_until = now() + timedelta(seconds=WORKER_LEASE_SECONDS)
            db.flush()


async def _heartbeat(run_id: str) -> None:
    while True:
        await asyncio.sleep(max(10, WORKER_LEASE_SECONDS // 3))
        try:
            await _extend_lease(run_id)
        except Exception:  # noqa: BLE001
            pass


async def process_one(run_id: str) -> None:
    if not _claim(run_id):
        log.debug("认领跳过 run=%s（已被其他 Worker 或状态不允许）", run_id)
        return
    run_id_var.set(run_id)
    log.info("开始执行 run=%s", run_id)
    hb = asyncio.create_task(_heartbeat(run_id))
    try:
        await execute_run(run_id)
        log.info("执行结束 run=%s", run_id)
    except Exception as exc:
        log.exception("执行异常 run=%s: %s", run_id, type(exc).__name__)
        RunExecutor(run_id)._finish_failed(f"执行初始化异常：{type(exc).__name__}")
    finally:
        hb.cancel()
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
    import time

    if once:
        run_id = await redis_bus.dequeue(timeout=2)
        pending = [run_id] if run_id else _recover_and_collect()
        if pending:
            await process_one(pending[0])
        return

    running: dict[str, asyncio.Task] = {}
    try:
        while not _stop:
            for rid, task in list(running.items()):
                if task.done():
                    running.pop(rid)
                    try:
                        task.result()
                    except Exception as exc:
                        log.exception("执行异常 run=%s: %s", rid, type(exc).__name__)
            # 清理任务与过期交互不再被一次长执行阻塞。
            if time.time() - last_reconcile > RECONCILE_INTERVAL:
                last_reconcile = time.time()
                # 事件与调用日志的过期清理按小时执行，避免每轮扫描整表。
                if time.time() - _last_prune > PRUNE_INTERVAL:
                    _last_prune = time.time()
                    with session_scope() as db:
                        prune_expired(db)
                # 8.6 告警：队列积压 / 执行卡死 / 失败率，默认每分钟一次
                if time.time() - _last_alert_check > ALERT_CHECK_INTERVAL:
                    _last_alert_check = time.time()
                    with session_scope() as db:
                        check_alerts(db)
                # 直接认领数据库里的 queued 执行，而不是往 Redis 再塞一份。
                # 之前每 5 秒重复 LPUSH 会让队列无限膨胀，Redis 断开时
                # 还依赖出队失败才走数据库路径；_claim 本身是原子的，直接跑更稳。
                for rid in _recover_and_collect():
                    if rid in running:
                        continue
                    if len(running) >= WORKER_CONCURRENCY:
                        break
                    running[rid] = asyncio.create_task(process_one(rid))
            if len(running) >= WORKER_CONCURRENCY:
                await asyncio.wait(list(running.values()), timeout=0.5, return_when=asyncio.FIRST_COMPLETED)
                continue
            run_id = await redis_bus.dequeue(timeout=1)
            if run_id and run_id not in running:
                running[run_id] = asyncio.create_task(process_one(run_id))
            await asyncio.sleep(0.1)
    finally:
        if running:
            await asyncio.gather(*running.values(), return_exceptions=True)
    log.info("Worker 退出")


if __name__ == "__main__":
    asyncio.run(main(once="--once" in sys.argv))
