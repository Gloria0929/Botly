"""用量、租约、限额和执行收尾。"""
from __future__ import annotations

import asyncio
import time
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from app.agent.messages import add_message
from app.core.config import DELEGATION_DEPTH
from app.core.db import session_scope
from app.core.events import emit, notify
from app.core.models import Chat, Message, Run, Task, Thread, UsageLog, UserSettings, now
from app.agent.runtime_helpers import (
    emit_run_status,
    emit_task_status,
    settle_run_tasks,
    stop_child_runs,
)
from app.agent.signals import LeaseLost, LimitExceeded, StopSignal


class RunFinishMixin:
    def _record_usage(
        self,
        *,
        run_id: str,
        task_id: str | None,
        bot_id: str,
        provider_id: str | None,
        model: str,
        usage: dict,
        latency_ms: int,
        error: str | None = None,
    ) -> int:
        tin = int(usage.get("input_tokens") or 0)
        tout = int(usage.get("output_tokens") or 0)
        with session_scope() as db:
            self._assert_lease(db)
            run = db.get(Run, run_id)
            if not run:
                return 0
            run.tokens_used += tin + tout
            task = db.get(Task, task_id) if task_id else None
            if task:
                task.tokens_used += tin + tout
            db.add(
                UsageLog(
                    user_id=run.user_id,
                    run_id=run_id,
                    task_id=task_id,
                    bot_id=bot_id,
                    provider_id=provider_id,
                    model=model,
                    input_tokens=tin,
                    output_tokens=tout,
                    latency_ms=latency_ms,
                    error=error,
                )
            )
            db.flush()
            agent_tokens = int(db.scalar(
                select(func.coalesce(func.sum(UsageLog.input_tokens + UsageLog.output_tokens), 0)).where(
                    UsageLog.user_id == run.user_id, UsageLog.run_id == run_id,
                    UsageLog.bot_id == bot_id, UsageLog.task_id.is_(None),
                )
            ) or 0)
            emit(
                db,
                run.user_id,
                run.chat_id,
                "usage.updated",
                {"run_id": run.id, "tokens_used": run.tokens_used, "steps_used": run.steps_used,
                 "bot_id": bot_id, "task_id": task_id, "agent_tokens": agent_tokens},
            )
            # 子任务的 token 用量要能实时显示在群聊里，不必等执行结束
            if task:
                db.flush()
                emit_task_status(db, task)
            budget = self._limits(db, run).get("token_budget", 300_000)
            if budget and run.tokens_used >= budget * 0.8:
                pct = int(run.tokens_used / budget * 100)
                already = db.scalar(
                    select(func.count(Message.id)).where(
                        Message.run_id == run.id,
                        Message.type == "system",
                        Message.content.like("已使用约%"),
                    )
                )
                if not already:
                    add_message(
                        db,
                        thread=self._main_thread(db, db.get(Chat, run.chat_id)),
                        run_id=run.id,
                        sender_type="system",
                        type_="system",
                        content=f"已使用约 {pct}% 的 token 预算（{run.tokens_used}/{budget}）。",
                    )
            return agent_tokens

    async def _record_usage_async(self, **kwargs) -> int:
        return await asyncio.to_thread(self._record_usage, **kwargs)

    def _limits(self, db: Session, run: Run) -> dict:
        settings = db.get(UserSettings, run.user_id)
        chat = db.get(Chat, run.chat_id)
        override = chat.limits_override or {}
        # RUN-09 / LIM-01：委派深度按「群聊覆盖 → 用户默认 → 环境变量」取值
        raw_depth = override.get("delegation_depth")
        if raw_depth is None and settings is not None:
            raw_depth = settings.delegation_depth
        try:
            depth = int(raw_depth) if raw_depth is not None else DELEGATION_DEPTH
        except (TypeError, ValueError):
            depth = DELEGATION_DEPTH
        return {
            "max_steps": 0,
            "token_budget": override.get("token_budget") if override.get("token_budget") is not None else (settings.token_budget if settings else 300_000),
            "delegation_depth": max(1, min(5, depth)),
        }

    def _check_limits(self) -> None:
        with session_scope() as db:
            run = db.get(Run, self.run_id)
            self._assert_lease(db, run)
            lim = self._limits(db, run)
            # RUN-12：并行子执行各自记账，预算必须按「根执行 + 所有子执行」
            # 合并算，否则一次并行 4 个子任务就能各花一份预算。
            root_id = run.parent_run_id or run.id
            family = [root_id] + list(
                db.scalars(select(Run.id).where(Run.parent_run_id == root_id))
            )
            used = int(
                db.scalar(
                    select(func.coalesce(func.sum(Run.tokens_used), 0)).where(
                        Run.id.in_(family)
                    )
                )
                or 0
            )
            if lim["token_budget"] > 0 and used >= lim["token_budget"]:
                raise LimitExceeded(
                    f"token 预算超限：已用 {used}，上限 {lim['token_budget']}"
                )
            if run.stop_requested:
                raise StopSignal()

    def _check_stop(self) -> None:
        with session_scope() as db:
            run = db.get(Run, self.run_id)
            self._assert_lease(db, run)
            if run and run.stop_requested:
                raise StopSignal()

    def _assert_lease(self, db: Session, run: Run | None = None) -> None:
        if not self.worker_id:
            return
        run = run or db.get(Run, self.run_id)
        if not self._owns_lease(run):
            raise LeaseLost()

    def _owns_lease(self, run: Run | None) -> bool:
        return not self.worker_id or bool(
            run and run.worker_id == self.worker_id and run.lease_token == self.lease_token
            and run.lease_until and run.lease_until > now()
        )

    async def _check_stop_throttled(self) -> None:
        t = time.time()
        if t - self._last_stop_check < 0.5:
            return
        self._last_stop_check = t
        await asyncio.to_thread(self._check_stop)

    def _close_run(self, status: str, reason: str) -> None:
        """只更新执行自身状态（子执行在自己的事务里收尾时用）。"""
        with session_scope() as db:
            run = db.get(Run, self.run_id)
            if not run or run.status in ("completed", "failed", "stopped"):
                return
            if not self._owns_lease(run):
                return
            run.status = status
            run.ended_at = now()
            run.end_reason = reason
            db.flush()
            emit_run_status(db, run)

    def _child_task_aborted(
        self, db: Session, run: Run, main: Thread, status: str, reason: str
    ) -> None:
        """并行子执行异常结束：落任务状态、回答 delegate、写批次汇总。"""
        task = db.get(Task, run.parent_task_id) if run.parent_task_id else None
        if not task or task.status in ("completed", "failed", "canceled"):
            return
        task.status = status
        task.result = reason
        task.ended_at = now()
        db.flush()
        emit_task_status(db, task)
        self._answer_delegate_call(db, main, task, self._delegate_summary(db, task))

    def _finish_completed_sync(self, content: str) -> bool:
        with session_scope() as db:
            run = db.get(Run, self.run_id)
            if not run or run.status in ("completed", "failed", "stopped") or not self._owns_lease(run):
                return False
            is_child = bool(run.parent_run_id)
            run.status = "completed"
            run.ended_at = now()
            run.end_reason = "已完成"
            # 正常结束时不应该还有活着的子执行；真有说明是异常竞态，一并停掉
            stop_child_runs(db, run, "父执行已结束")
            settle_run_tasks(db, run, "canceled", "执行已结束")
            db.flush()
            emit_run_status(db, run)
            chat = db.get(Chat, run.chat_id)
            chat.last_active_at = now()
            if not is_child:
                notify(
                    db,
                    run.user_id,
                    "run_completed",
                    f"「{chat.name}」执行完成",
                    (content or "")[:120],
                    chat_id=chat.id,
                    target_type="run",
                    target_id=run.id,
                )
        return True

    def _finish_stopped(self) -> None:
        parent_id = None
        with session_scope() as db:
            run = db.get(Run, self.run_id)
            if not run or not self._owns_lease(run):
                return
            if run.status == "stopped":
                settle_run_tasks(db, run, "canceled", "用户停止")
                return
            is_child = bool(run.parent_run_id)
            run.status = "stopped"
            run.ended_at = now()
            run.end_reason = "用户停止"
            stop_child_runs(db, run, "上游执行已停止")
            settle_run_tasks(db, run, "canceled", run.end_reason)
            main = self._main_thread(db, db.get(Chat, run.chat_id))
            if is_child:
                self._child_task_aborted(db, run, main, "canceled", "用户停止")
                parent_id = run.parent_run_id
            else:
                db.flush()
                add_message(
                    db,
                    thread=main,
                    run_id=run.id,
                    sender_type="system",
                    type_="system",
                    content="执行已被用户停止，已完成的步骤与产物保留。",
                )
            db.flush()
            emit_run_status(db, run)
        if parent_id:
            self._wake_parent(parent_id)

    def _finish_limited(self, reason: str) -> None:
        parent_id = None
        with session_scope() as db:
            run = db.get(Run, self.run_id)
            if not run or run.status in ("completed", "failed", "stopped") or not self._owns_lease(run):
                return
            is_child = bool(run.parent_run_id)
            run.status = "failed"
            run.ended_at = now()
            run.end_reason = reason
            stop_child_runs(db, run, f"上游执行停止：{reason}")
            settle_run_tasks(db, run, "failed", reason)
            db.flush()
            emit_run_status(db, run)
            chat = db.get(Chat, run.chat_id)
            main = self._main_thread(db, chat)
            if is_child:
                self._child_task_aborted(db, run, main, "failed", f"失败：{reason}")
                parent_id = run.parent_run_id
            else:
                add_message(
                    db,
                    thread=main,
                    run_id=run.id,
                    sender_type="system",
                    type_="limit",
                    content=f"执行已自动停止：{reason}。已完成的步骤与产物保留。",
                )
                notify(
                    db,
                    run.user_id,
                    "run_failed",
                    f"「{chat.name}」执行失败",
                    reason,
                    chat_id=chat.id,
                    target_type="run",
                    target_id=run.id,
                )
        if parent_id:
            self._wake_parent(parent_id)

    def _finish_failed(self, reason: str) -> None:
        parent_id = None
        with session_scope() as db:
            run = db.get(Run, self.run_id)
            if not run or run.status in ("completed", "failed", "stopped") or not self._owns_lease(run):
                return
            is_child = bool(run.parent_run_id)
            run.status = "failed"
            run.ended_at = now()
            run.end_reason = reason
            stop_child_runs(db, run, f"上游执行失败：{reason}")
            settle_run_tasks(db, run, "failed", reason)
            db.flush()
            emit_run_status(db, run)
            chat = db.get(Chat, run.chat_id)
            main = self._main_thread(db, chat)
            if is_child:
                self._child_task_aborted(db, run, main, "failed", f"失败：{reason}")
                parent_id = run.parent_run_id
            else:
                add_message(
                    db,
                    thread=main,
                    run_id=run.id,
                    sender_type="system",
                    type_="system",
                    content=reason,
                )
                notify(
                    db,
                    run.user_id,
                    "run_failed",
                    f"「{chat.name}」执行失败",
                    reason[:160],
                    chat_id=chat.id,
                    target_type="run",
                    target_id=run.id,
                )
        if parent_id:
            self._wake_parent(parent_id)
