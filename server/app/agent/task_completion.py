"""子任务结果、交接和父执行唤醒。"""
from __future__ import annotations

import asyncio
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from langgraph.types import interrupt
from app.agent.messages import add_message
from app.core.db import session_scope
from app.core.events import emit, notify
from app.agent.execution_config import execution_bot, supervisor_id
from app.core.models import Bot, Chat, Message, Run, Task, Thread, now
from app.core.redis_bus import enqueue
from app.agent.runtime_helpers import (
    CHILD_ACTIVE_STATUSES,
    EMPTY_OUTPUT,
    add_compat_message,
    emit_run_status,
    emit_task_status,
    log,
)


class TaskCompletionMixin:
    def _publish_task_result(
        self, db: Session, run: Run, chat: Chat, main: Thread, task: Task, content: str
    ) -> bool:
        """子任务完成：回写任务、在群聊里给出结果、回答 delegate。返回是否首次完成。

        串行（子 Bot 就地跑在总指挥的图里）和并行（独立子执行）共用这段逻辑，
        区别只在于 live 镜像消息属于哪个 run。
        """
        if task.status in ("completed", "failed", "canceled"):
            return False
        bot = execution_bot(db, run, task.assigned_bot_id)
        task.status = "completed"
        task.result = content or EMPTY_OUTPUT
        task.ended_at = now()
        task.tokens_used = task.tokens_used or 0
        db.flush()
        emit_task_status(db, task)
        elapsed = ""
        if task.started_at and task.ended_at:
            elapsed = f"{int((task.ended_at - task.started_at).total_seconds())} 秒"
        log.info(
            "任务完成 task=%s 执行者=%s 耗时=%s tokens=%s",
            task.id, bot.name if bot else "", elapsed, task.tokens_used,
        )
        result_meta = {
            "task_id": task.id,
            "thread_id": task.thread_id,
            "subtask": True,
            "bot_name": bot.name if bot else "",
            "elapsed": elapsed,
            "tokens": task.tokens_used,
            "batch": task.batch or 0,
        }
        # 如果最后一轮已经通过群聊镜像把答案实时发出来了，就把那条消息升级成
        # 完成消息，避免同一条回复出现两次。用 Python 过滤 meta，兼容 SQLite/Postgres。
        live = next(
            (
                m
                for m in db.scalars(
                    select(Message)
                    .where(
                        Message.thread_id == main.id,
                        Message.run_id == run.id,
                        Message.type == "text",
                    )
                    .order_by(Message.seq.desc())
                ).all()
                if (m.meta or {}).get("task_id") == task.id
                and (m.meta or {}).get("subtask_live")
            ),
            None,
        )
        if live and (live.content or "").strip() == (task.result or "").strip():
            promoted = {**(live.meta or {}), **result_meta}
            promoted.pop("subtask_live", None)
            live.meta = promoted
            db.flush()
            emit(
                db,
                run.user_id,
                run.chat_id,
                "message.completed",
                {
                    "message_id": live.id,
                    "content": live.content,
                    "status": live.status,
                    "meta": live.meta,
                },
            )
        elif run.kind != "free":
            # 子 Bot 在群聊里以自己的身份回复，读起来就是一次对话
            add_message(
                db,
                thread=main,
                run_id=run.id,
                sender_type="bot",
                type_="text",
                content=task.result,
                sender_bot_id=task.assigned_bot_id,
                meta=result_meta,
            )
        # 总指挥那条 delegate 还悬着，补上结果它才能继续
        self._answer_delegate_call(db, main, task, self._delegate_summary(db, task))
        # CHT-05 / RUN-08 / 7.1：完成卡片与交接提示的结构化记录（前端不额外渲染）
        done_bot = bot.name if bot else "子 Bot"
        # RUN-09：嵌套派任务的结果交回上级成员，而不是总指挥
        parent = self._delegation_parent(db, task)
        parent_bot = db.get(Bot, parent[0].bot_id) if parent and parent[0].bot_id else None
        handoff_to = f"「{parent_bot.name}」" if parent_bot else "总指挥"
        add_compat_message(
            db,
            thread=main,
            run_id=run.id,
            type_="task_done",
            content=(task.result or "")[:1500],
            sender_bot_id=task.assigned_bot_id,
            meta={
                "task_id": task.id,
                "thread_id": task.thread_id,
                "bot_id": task.assigned_bot_id,
                "bot_name": done_bot,
                "status": "completed",
                "elapsed": elapsed,
                "tokens": task.tokens_used,
                "batch": task.batch or 0,
            },
        )
        add_compat_message(
            db,
            thread=main,
            run_id=run.id,
            type_="handoff",
            content=f"「{done_bot}」已完成子任务，结果已交回{handoff_to}。",
            sender_type="system",
            meta={
                "task_id": task.id,
                "thread_id": task.thread_id,
                "status": "completed",
            },
        )
        db.flush()
        return True

    def _maybe_write_batch_summary(
        self, db: Session, main: Thread, task: Task
    ) -> None:
        """同一批并行子任务全部返回后，给总指挥留一条汇总系统消息。

        模型自己发起的 delegate 已有各自的 tool_result；被系统补齐的步骤
        （计划里有、但模型没单独发调用）没有对应消息，汇总保证它们的结果
        也能进入总指挥的上下文。按 batch 去重，节点重放不会写第二条。
        """
        if not task.batch:
            return
        siblings = db.scalars(
            select(Task).where(Task.run_id == task.run_id, Task.batch == task.batch)
        ).all()
        # 改派会产生第二次尝试：同一个 step_key 只保留最新一条，汇总不重复
        latest: dict[str, Task] = {}
        for item in siblings:
            key = item.step_key or item.id
            if key not in latest or item.created_at > latest[key].created_at:
                latest[key] = item
        siblings = list(latest.values())
        if any(
            t.status in ("queued", "running", "waiting_approval", "waiting_reply")
            for t in siblings
        ):
            return
        marker = f"并行批次 {task.batch} "
        exists = db.scalar(
            select(func.count(Message.id)).where(
                Message.run_id == task.run_id,
                Message.type == "system",
                Message.content.like(marker + "%"),
            )
        )
        if exists:
            return
        bots = {
            b.id: b.name
            for b in db.scalars(
                select(Bot).where(Bot.id.in_([t.assigned_bot_id for t in siblings]))
            ).all()
        }
        lines = []
        for t in siblings:
            head = (t.instruction or "").split("\n", 1)[0][:40]
            state = {"completed": "完成", "failed": "失败", "canceled": "取消"}.get(
                t.status, t.status
            )
            lines.append(f"- {bots.get(t.assigned_bot_id, '')} · {head}（{state}）：\n"
                         + self._delegate_summary(db, t))
        add_message(
            db,
            thread=main,
            run_id=task.run_id,
            sender_type="system",
            type_="system",
            content=f"并行批次 {task.batch} 已全部返回：\n" + "\n".join(lines),
            meta={"kind": "parallel_batch", "batch": task.batch},
        )
        db.flush()

    def _finish_task_db(self, task_id: str, content: str) -> dict:
        self._check_stop()
        parent_frame: dict | None = None
        with session_scope() as db:
            run = db.get(Run, self.run_id)
            self._assert_lease(db, run)
            chat = db.get(Chat, run.chat_id)
            main = self._main_thread(db, chat)
            task = db.get(Task, task_id)
            sup_id = supervisor_id(run, chat)
            sup = execution_bot(db, run, sup_id) if sup_id else None
            parent = self._delegation_parent(db, task)
            step_mode = chat.run_mode == "step" and not parent
            task_title = (task.instruction or "")[:60]
            published = self._publish_task_result(db, run, chat, main, task, content)
            if published and step_mode:
                notify(
                    db, run.user_id, "step", f"「{chat.name}」已暂停，等待你继续", task_title,
                    chat_id=run.chat_id, target_type="run", target_id=run.id,
                )
            main_id = main.id
            sup_id = sup.id if sup else None
            task_id_value = task.id
            if parent:
                parent_thread, parent_task = parent
                parent_frame = {
                    "role": "worker", "bot_id": parent_thread.bot_id,
                    "thread_id": parent_thread.id, "task_id": parent_task.id,
                }
            elif run.kind == "free":
                selected = run.config_snapshot["free"]["selected"]
                parent_frame = {"role": "peer", "bot_id": selected["bot_id"],
                                "thread_id": main.id, "task_id": None}
                step_mode = False
        return {
            "parent_frame": parent_frame, "step_mode": step_mode, "task_title": task_title,
            "task_id": task_id_value, "main_id": main_id, "sup_id": sup_id,
        }

    async def finish_task(self, *, task_id: str, content: str) -> dict:
        """子 Bot 跑完：回写结果，切回派任务的那一层。

        直接子任务回到总指挥（逐步确认模式下在这里暂停）；嵌套派任务回到上级成员，
        由上级继续汇总，不打断用户（RUN-09）。
        """
        result = await asyncio.to_thread(self._finish_task_db, task_id, content)
        parent_frame = result["parent_frame"]
        if parent_frame:
            return {**parent_frame, "last": "", "has_calls": False, "cont": True}
        if result["step_mode"]:
            interrupt({
                "kind": "step", "phase": "supervisor", "task_id": result["task_id"],
                "task_title": result["task_title"],
            })
        return {
            "role": "supervisor", "bot_id": result["sup_id"], "thread_id": result["main_id"],
            "task_id": None, "last": "", "has_calls": False, "cont": True,
        }

    async def finish_child_task(self, *, task_id: str, content: str) -> dict:
        result, completed = await asyncio.to_thread(
            self._finish_child_task_sync, task_id=task_id, content=content
        )
        if completed:
            self._schedule_memory_candidates(content)
        return result

    def _finish_child_task_sync(self, *, task_id: str, content: str) -> dict:
        """并行子执行跑完：回写任务与群聊，关掉自己这条执行并唤醒父执行。"""
        main_id = ""
        parent_id = None
        with session_scope() as db:
            run = db.get(Run, self.run_id)
            self._assert_lease(db, run)
            chat = db.get(Chat, run.chat_id)
            main = self._main_thread(db, chat)
            task = db.get(Task, task_id)
            if task:
                # 批次汇总统一在父执行恢复时写（_ensure_batch_summaries）：
                # 多个子执行同时结束，在各自主事务里判断「是不是最后一个」会漏写或重写
                self._publish_task_result(db, run, chat, main, task, content)
            main_id = main.id
            parent_id = run.parent_run_id
        completed = self._finish_completed_sync(content)
        if parent_id:
            self._wake_parent(parent_id)
        return {
            "role": "worker",
            "bot_id": None,
            "thread_id": main_id,
            "task_id": task_id,
            "last": content,
            "has_calls": False,
            "cont": False,
            "done": True,
        }, completed

    def _wake_parent(self, parent_id: str) -> None:
        """所有并行子执行都结束了，把等待中的父执行重新入队。"""
        with session_scope() as db:
            parent = db.get(Run, parent_id)
            if not parent or parent.status != "waiting_children":
                return
            active = db.scalar(
                select(func.count(Run.id)).where(
                    Run.parent_run_id == parent.id, Run.status.in_(CHILD_ACTIVE_STATUSES)
                )
            )
            if active and not (parent.kind == "free" and self._free_ready(db, parent)):
                return
            parent.status = "queued"
            db.flush()
            emit_run_status(db, parent)
        enqueue(parent_id)

    async def fail_task(self, *, task_id: str, reason: str) -> dict:
        return await asyncio.to_thread(self._fail_task_sync, task_id=task_id, reason=reason)

    def _fail_task_sync(self, *, task_id: str, reason: str) -> dict:
        """子任务模型失败：记失败原因，把控制权交回派任务的那一层。

        嵌套派任务（RUN-09）失败只交回上级成员，不结束整个执行；
        并行子执行的顶层任务失败才关闭这条子执行。
        """
        self._check_stop()
        parent_id = None
        is_child = False
        parent_frame: dict | None = None
        with session_scope() as db:
            run = db.get(Run, self.run_id)
            self._assert_lease(db, run)
            is_child = bool(run.parent_run_id or (run.config_snapshot or {}).get("retry_task_id"))
            chat = db.get(Chat, run.chat_id)
            main = self._main_thread(db, chat)
            task = db.get(Task, task_id)
            parent = self._delegation_parent(db, task)
            sup_id = supervisor_id(run, chat)
            sup = execution_bot(db, run, sup_id) if sup_id else None
            if task and task.status not in ("completed", "failed", "canceled"):
                task.status = "failed"
                task.result = f"失败：{reason}"
                task.ended_at = now()
                db.flush()
                emit_task_status(db, task)
                log.warning("任务失败 task=%s 原因=%s", task.id, str(reason)[:500])
                self._answer_delegate_call(db, main, task, self._delegate_summary(db, task))
                # CHT-05 / RUN-08 / 7.1：失败也出完成卡与交接提示（结构化记录，前端不渲染）
                failed_bot = db.get(Bot, task.assigned_bot_id)
                failed_name = failed_bot.name if failed_bot else "子 Bot"
                parent_bot = db.get(Bot, parent[0].bot_id) if parent and parent[0].bot_id else None
                handoff_to = f"「{parent_bot.name}」" if parent_bot else "总指挥"
                add_compat_message(
                    db,
                    thread=main,
                    run_id=run.id,
                    type_="task_done",
                    content=f"子任务失败：{reason}"[:1500],
                    sender_bot_id=task.assigned_bot_id,
                    meta={
                        "task_id": task.id,
                        "thread_id": task.thread_id,
                        "bot_id": task.assigned_bot_id,
                        "bot_name": failed_name,
                        "status": "failed",
                        "elapsed": "",
                        "tokens": task.tokens_used,
                        "batch": task.batch or 0,
                    },
                )
                add_compat_message(
                    db,
                    thread=main,
                    run_id=run.id,
                    type_="handoff",
                    content=f"「{failed_name}」的子任务失败，控制权已交回{handoff_to}。",
                    sender_type="system",
                    meta={
                        "task_id": task.id,
                        "thread_id": task.thread_id,
                        "status": "failed",
                    },
                )
                if not is_child:
                    add_message(
                        db,
                        thread=main,
                        run_id=run.id,
                        sender_type="system",
                        type_="system",
                        content=f"子任务失败：{reason}",
                    )
                db.flush()
            main_id = main.id
            sup_id = sup.id if sup else None
            parent_id = run.parent_run_id
            if parent:
                parent_thread, parent_task = parent
                parent_frame = {
                    "role": "worker",
                    "bot_id": parent_thread.bot_id,
                    "thread_id": parent_thread.id,
                    "task_id": parent_task.id,
                }
            elif run.kind == "free":
                parent_frame = {"role": "peer", "bot_id": run.config_snapshot["free"]["selected"]["bot_id"],
                                "thread_id": main.id, "task_id": None}
        if parent_frame:
            # 嵌套子任务失败：交回上级成员，由它决定改派/跳过/继续
            return {**parent_frame, "last": "", "has_calls": False, "cont": True}
        if is_child:
            if parent_id:
                self._close_run("failed", f"子任务失败：{reason}")
            else:
                self._finish_failed(f"子任务失败：{reason}")
            if parent_id:
                self._wake_parent(parent_id)
            return {
                "role": "worker",
                "bot_id": None,
                "thread_id": main_id,
                "task_id": task_id,
                "last": "",
                "has_calls": False,
                "cont": False,
                "done": True,
            }
        return {
            "role": "supervisor",
            "bot_id": sup_id,
            "thread_id": main_id,
            "task_id": None,
            "last": "",
            "has_calls": False,
            "cont": True,
        }

    async def finish_run(self, content: str) -> None:
        completed = await asyncio.to_thread(self._finish_completed_sync, content)
        if completed:
            self._schedule_memory_candidates(content)
            await asyncio.to_thread(self._wake_free_peer_parent)

    def _wake_free_peer_parent(self) -> None:
        with session_scope() as db:
            run = db.get(Run, self.run_id)
            parent_id = run.parent_run_id if run and run.kind == "free" else None
        if parent_id:
            self._wake_parent(parent_id)
