"""计划批次与任务派发。"""
from __future__ import annotations

import asyncio
from sqlalchemy import select
from sqlalchemy.orm import Session
from app.agent.messages import add_message
from app.tools import registry as toolmod
from app.core.config import MAX_REASSIGN
from app.core.db import session_scope
from app.agent.execution_config import supervisor_id
from app.agent.scheduling import schedule_plan
from app.core.models import Bot, Chat, FileRec, Message, Run, Task, Thread, now
from app.core.redis_bus import enqueue
from app.agent.runtime_helpers import (
    _parse_json,
    _step_key,
    add_compat_message,
    emit_task_status,
    log,
)
from app.agent.signals import AwaitChildren


class DelegationMixin:
    def _resolve_member(self, members: list[Bot], name: str) -> Bot | None:
        if not name:
            return None
        return next((b for b in members if b.name == name), None) or next(
            (b for b in members if name in b.name), None
        )

    def _task_for_call(
        self, db: Session, run_id: str, main_id: str, call_id: str | None,
        thread_id: str | None = None,
    ) -> Task | None:
        """按 delegate 的 tool_call_id 找到这次派任务建出来的任务。

        嵌套派任务（RUN-09）的 tool_call 发生在上级成员的线程里，但展示用的
        「@成员 指令」仍然写在主线程，所以按 note 上的 delegate_thread_id 区分，
        避免不同层级复用了同一个 call id 时互相串任务。
        """
        if not call_id:
            return None
        for note in db.scalars(
            select(Message).where(
                Message.run_id == run_id, Message.thread_id == main_id, Message.type == "text",
            )
        ):
            meta = note.meta or {}
            if not meta.get("delegation") or call_id not in [
                meta.get("tool_call_id"), *(meta.get("delegate_call_ids") or []),
            ]:
                continue
            if thread_id and (meta.get("delegate_thread_id") or main_id) != thread_id:
                continue
            task = db.get(Task, meta.get("task_id"))
            if task:
                return task
        return None

    def _call_answered(self, db: Session, thread_id: str, run_id: str, call_id: str | None) -> bool:
        if not call_id:
            return False
        row = db.scalar(
            select(Message).where(
                Message.thread_id == thread_id,
                Message.run_id == run_id,
                Message.type == "tool_result",
                Message.meta["tool_call_id"].as_string() == call_id,
            )
        )
        return row is not None

    @staticmethod
    def _approved_plan_steps(db: Session, run_id: str) -> list[dict]:
        """本次执行里用户最后确认过的那版计划。"""
        rows = db.scalars(
            select(Message)
            .where(Message.run_id == run_id, Message.type.in_(("plan", "system")))
            .order_by(Message.seq.desc())
        ).all()
        for msg in rows:
            meta = msg.meta or {}
            if (meta.get("status") == "approved" or meta.get("kind") == "plan_adjusted") and meta.get("steps"):
                return schedule_plan(meta["steps"])
        return []

    @staticmethod
    def _plan_batches(steps: list[dict]) -> list[list[tuple[int, dict]]]:
        """按 batch 给计划步骤分组，保持计划顺序。

        没写 batch 的步骤各自成一组（相当于串行），只有相同 batch 号的步骤
        才会并行。分组放在执行时算，用户改过计划也能按最终版本执行。
        """
        groups: dict[int, list[tuple[int, dict]]] = {}
        order: list[int] = []
        for idx, step in enumerate(steps):
            if not isinstance(step, dict):
                continue
            raw = step.get("batch")
            key = raw if type(raw) is int and raw > 0 else -(idx + 1)
            if key not in groups:
                groups[key] = []
                order.append(key)
            groups[key].append((idx, step))
        return [groups[k] for k in order]

    @staticmethod
    def _match_plan_step(steps: list[dict], key: str) -> dict | None:
        """完整标题优先，其次序号，简称只允许唯一匹配。"""
        exact = [step for step in steps if _step_key(step.get("title") or "") == key]
        if exact:
            return exact[0] if len(exact) == 1 else None
        if key.isdecimal() and 1 <= int(key) <= len(steps):
            return steps[int(key) - 1]
        matches = [step for step in steps if len(key) >= 4
                   and (title := _step_key(step.get("title") or ""))
                   and (key in title or title in key)]
        return matches[0] if len(matches) == 1 else None

    @staticmethod
    def _latest_plan_tasks(db: Session, run_id: str, main_id: str) -> dict[str, Task]:
        """仅总指挥直接派发的最新尝试能满足计划依赖，嵌套同名任务不计入。"""
        latest: dict[str, Task] = {}
        for task in db.scalars(select(Task).join(Thread, Thread.id == Task.thread_id).where(
            Task.run_id == run_id, (Thread.parent_thread_id == main_id) | (Task.thread_id == main_id),
        ).order_by(Task.created_at.desc())):
            latest.setdefault(_step_key(task.step_key or task.instruction), task)
        return latest

    @staticmethod
    def _blocked_dependencies(step: dict, latest: dict[str, Task]) -> list[str]:
        return [title for title in step.get("depends_on", [])
                if (task := latest.get(_step_key(title))) is None or task.status != "completed"]

    def _parallel_batch(
        self, db: Session, run: Run, chat: Chat, step_key: str
    ) -> tuple[int, list[dict], str] | None:
        """当前步骤属于某个「同批 ≥2 步」的计划批次时，返回批次信息。"""
        # 逐步确认模式要求每个子任务都停下来等用户，和并行天然冲突；
        # 并行子执行与子 Bot 的嵌套派任务都走串行（计划批次只属于总指挥）。
        if run.parent_run_id or chat.run_mode == "step":
            return None
        steps = self._approved_plan_steps(db, run.id)
        matched = self._match_plan_step(steps, step_key)
        if not matched:
            return None
        for group in self._plan_batches(steps):
            if len(group) < 2:
                continue
            for _, step in group:
                if step is not matched:
                    continue
                batch_no = step.get("batch") if isinstance(step.get("batch"), int) else 1
                return int(batch_no), [s for _, s in group], _step_key(step.get("title") or "")
        return None

    @staticmethod
    def _plan_instruction(step: dict, batch_steps: list[dict]) -> str:
        """模型没给某一步写指令时，用计划标题 + 期望产出拼一条可执行的指令。"""
        title = (step.get("title") or "").strip()
        others = "、".join(
            (s.get("title") or "").strip()
            for s in batch_steps
            if (s.get("title") or "").strip() and s is not step
        )
        parts = [title]
        if others:
            parts.append(f"同批其他步骤「{others}」由各自任务负责，只完成本步骤，不要重复其他步骤的工作。")
        expected = (step.get("expected") or "").strip()
        if expected:
            parts.append(f"期望产出：{expected}")
        return "\n\n".join(p for p in parts if p)

    def _delegate_summary(self, db: Session, task: Task) -> str:
        bot = db.get(Bot, task.assigned_bot_id)
        identity = f"任务 ID：{task.id}；步骤：{task.step_key or task.instruction[:80]}。\n"
        if task.status == "failed":
            head = f"子任务执行失败：{task.result or '未知原因'}。你可以改派给其他成员，或跳过该步骤并向用户说明。"
            return identity + head
        if task.status == "canceled":
            return identity + f"子任务已取消：{task.result or '用户停止'}"
        execution_ids = [task.run_id, *db.scalars(select(Run.id).where(Run.parent_task_id == task.id))]
        files = [f.path for f in db.scalars(select(FileRec).where(
            FileRec.user_id == task.user_id, FileRec.run_id.in_(execution_ids),
            FileRec.created_by_bot_id == task.assigned_bot_id,
        ).order_by(FileRec.path)) if f.path in (task.result or "")
                 or (f.path.startswith(("tasks/", "output/")) and f.path.rsplit("/", 1)[-1] in (task.result or ""))]
        result = task.result or ""
        if len(result) > 3000:
            result = result[:2000] + "\n[中间内容已截断，完整结果保留在任务详情]\n" + result[-1000:]
        return (
            identity + f"任务已完成。执行者：{bot.name if bot else ''}。\n"
            f"期望产出：{task.expected_output or '未指定'}\n"
            f"结果摘要：\n{result}\n"
            f"相关产物记录：{'、'.join(files) if files else '本结果未关联产物记录'}。\n"
            "请依据期望产出、实际结果及验证证据验收，完成状态不代表已经验收通过。"
            "先复用已有成果；需要修正时只派发具体的增量修订，不要原样重派。"
        )

    def _start_parallel_batch(
        self,
        db: Session,
        run: Run,
        chat: Chat,
        main: Thread,
        members: list[Bot],
        batch_no: int,
        steps: list[dict],
        matched_key: str,
        call: dict,
        args: dict,
        bot_id: str,
    ) -> tuple[list[str], bool]:
        """给同一批的每个步骤建任务 + 子执行，返回 (待入队的子执行, 是否仍在跑)。

        模型可能在同一轮里已经为其中几步发过 delegate（支持并行工具调用的模型），
        这里按 step 建索引优先复用它的指令；没发过的步骤用计划标题补一条。
        """
        ask_by_step: dict[str, tuple[dict, dict]] = {}
        plan = self._approved_plan_steps(db, run.id)
        for msg in self._open_tool_calls(db, main.id, run.id):
            c = ((msg.meta or {}).get("tool_calls") or [{}])[0]
            fn = c.get("function") or {}
            if fn.get("name") != "delegate":
                continue
            a = _parse_json(fn.get("arguments") or "{}")
            raw_key = a.get("step") or a.get("instruction") or ""
            if not isinstance(raw_key, str):
                continue  # 交回常规工具校验，不能让其他调用的坏参数中断本批。
            key = _step_key(raw_key)
            matched = self._match_plan_step(plan, key)
            if matched:
                ask_by_step.setdefault(_step_key(matched["title"]), (c, a))
        ask_by_step[matched_key] = (call, args)

        latest = self._latest_plan_tasks(db, run.id, main.id)
        steps = [step for step in steps if not self._blocked_dependencies(step, latest)]
        # 先校验全部待补派的步骤，避免建了一半任务才发现成员停用或参数无效。
        assignments = {}
        for step in steps:
            key = _step_key(step["title"])
            if key in latest:
                continue
            ask = ask_by_step.get(key)
            if ask:
                error = toolmod.validate_args(toolmod.DELEGATE_TOOL, ask[1])
                if error:
                    raise ValueError(f"「{step['title']}」派发参数不合法：{error}")
            target_name = ask[1]["bot"] if ask else step.get("bot") or ""
            target = self._resolve_member(members, target_name)
            if not target or target.enabled is False or target.id == bot_id:
                raise ValueError(f"「{step['title']}」的执行者「{target_name}」不存在、已停用或是总指挥本人，请调整执行者后重试")
            instruction = ((ask[1].get("instruction") if ask else "") or "").strip()
            if ask and not instruction:
                raise ValueError(f"「{step['title']}」指令为空，请补充具体任务内容")
            assignments[key] = (target, instruction or self._plan_instruction(step, steps),
                                (ask[1].get("expected_output") if ask else "") or step.get("expected") or "")

        child_ids: list[str] = []
        keys: list[str] = []
        for step in steps:
            key = _step_key(step.get("title") or "")
            keys.append(key)
            task = latest.get(key)
            if task:
                # 显式重试/修订留给该调用自行执行；补派只能复用完全相同的已完成工作。
                ask = ask_by_step.get(key)
                if task.status == "completed" and ask and not toolmod.validate_args(toolmod.DELEGATE_TOOL, ask[1]):
                    target = self._resolve_member(members, ask[1]["bot"])
                    expected = ask[1].get("expected_output") or step.get("expected") or ""
                    if (target and target.id == task.assigned_bot_id
                            and ask[1]["instruction"].strip() == task.instruction
                            and expected == (task.expected_output or "") and ask[0].get("id")):
                        self._answer_open_calls(db, main, run.id, self._delegate_summary(db, task),
                                                call_id=ask[0]["id"])
                continue

            target, instruction, expected = assignments[key]
            ask = ask_by_step.get(key)

            thread = Thread(
                user_id=run.user_id,
                chat_id=chat.id,
                parent_thread_id=main.id,
                type="task",
                title=instruction[:60],
                bot_id=target.id,
            )
            db.add(thread)
            db.flush()
            task = Task(
                user_id=run.user_id,
                run_id=run.id,
                thread_id=thread.id,
                assigned_bot_id=target.id,
                instruction=instruction,
                expected_output=expected,
                attempt=1,
                step_key=key,
                batch=batch_no,
                status="queued",
            )
            db.add(task)
            db.flush()
            latest[key] = task
            note_meta = {
                "task_id": task.id,
                "thread_id": thread.id,
                "mention_bot_id": target.id,
                "mention_name": target.name,
                "expected_output": expected,
                "delegation": True,
                "delegation_instruction": instruction,
                "batch": batch_no,
                "parallel": True,
            }
            if ask and ask[0].get("id"):
                note_meta["tool_call_id"] = ask[0]["id"]
            # 群聊里仍然是一条「@成员 指令」，读起来和串行派任务一致；
            # batch 标记让前端把同批的消息收拢成一组。
            add_message(
                db,
                thread=main,
                run_id=run.id,
                sender_type="bot",
                type_="text",
                content=f"@{target.name} {instruction}",
                sender_bot_id=bot_id,
                meta=note_meta,
            )
            # CHT-05 / RUN-08：任务卡片的结构化记录（前端按 compat 过滤不渲染）
            add_compat_message(
                db,
                thread=main,
                run_id=run.id,
                type_="task",
                content=instruction,
                sender_bot_id=bot_id,
                meta={
                    "task_id": task.id,
                    "thread_id": thread.id,
                    "bot_id": target.id,
                    "bot_name": target.name,
                    "expected_output": expected,
                    "status": "queued",
                    "attempt": task.attempt,
                    "batch": batch_no,
                    "parallel": True,
                },
            )
            child = Run(
                user_id=run.user_id,
                chat_id=chat.id,
                status="queued",
                mode=run.mode,
                target_bot_id=target.id,
                thread_id=thread.id,
                parent_run_id=run.id,
                parent_task_id=task.id,
                config_snapshot={**(run.config_snapshot or {}), "task_workspace": f"tasks/{task.id}"},
            )
            db.add(child)
            db.flush()
            child_ids.append(child.id)
            emit_task_status(db, task)
            log.info(
                "并行派任务 task=%s 批次=%s 执行者=%s 指令=%r",
                task.id, batch_no, target.name, instruction[:120],
            )

        pending = any(
            latest[key].status in ("queued", "running", "waiting_approval", "waiting_reply")
            for key in keys if key in latest
        )
        return child_ids, pending

    async def start_delegate(
        self, *, thread_id: str, bot_id: str, task_id: str | None, call: dict
    ) -> dict:
        return await asyncio.to_thread(
            self._start_delegate_sync,
            thread_id=thread_id, bot_id=bot_id, task_id=task_id, call=call,
        )

    def _start_delegate_sync(
        self, *, thread_id: str, bot_id: str, task_id: str | None, call: dict
    ) -> dict:
        """派任务：串行时切换角色就地执行，同批计划步骤则拉起并行子执行后等待。"""
        args = _parse_json((call.get("function") or {}).get("arguments") or "{}")
        error = toolmod.validate_args(toolmod.DELEGATE_TOOL, args)
        if error:
            self.write_tool_result(thread_id, call, f"参数不合法：{error}")
            return {}
        instruction = (args.get("instruction") or "").strip()
        expected = args.get("expected_output") or ""
        target_name = args.get("bot") or ""
        # 稳定键优先取计划步骤（模型可原样回传），没给才退回指令文本
        step_key = _step_key(args.get("step") or instruction)
        if not instruction:
            self.write_tool_result(thread_id, call, "指令为空，请补充具体任务内容。")
            return {}

        deferred = False
        revision = False
        retry = False
        enqueue_ids: list[str] = []
        patch: dict = {}
        with session_scope() as db:
            run = db.get(Run, self.run_id)
            self._assert_lease(db, run)
            chat = db.get(Chat, run.chat_id)
            members = self._member_bots(db, chat, run)
            target = self._resolve_member(members, target_name)
            if not target:
                names = "、".join(b.name for b in members)
                self.write_tool_result(
                    thread_id, call, f"没有名为「{target_name}」的成员。可选成员：{names}"
                )
                return {}
            # BOT-06：停用的成员不接活，明确告诉模型换人，别让它反复重试
            if target.enabled is False:
                names = "、".join(b.name for b in members if b.enabled is not False)
                self.write_tool_result(
                    thread_id, call,
                    f"「{target.name}」已停用，不能接受任务。可派给：{names}",
                )
                return {}
            if target.id == bot_id:
                self.write_tool_result(thread_id, call, "不能把任务派给自己。")
                return {}
            if run.kind == "free":
                state = dict(run.config_snapshot["free"])
                participants = set(state["spoken"]) | {state["selected"]["bot_id"]}
                participants.update(db.scalars(select(Task.assigned_bot_id).where(Task.run_id == run.id)))
                if target.id not in participants and len(participants) >= state["max_speakers"]:
                    self.write_tool_result(thread_id, call, "本条消息已达到参与人数上限，请在群里说明并等待下一条用户消息。")
                    return {}

            # RUN-09：子 Bot 也能继续派任务，但不能超过委派深度，也不能派回上级。
            call_thread = db.get(Thread, thread_id)
            if call_thread and call_thread.type == "branch":
                self.write_tool_result(thread_id, call, "分支讨论不能派任务，请直接回答或回到主线程发起任务。")
                return {}
            nested = bool(call_thread and call_thread.type == "task")
            depth = self._thread_depth(db, thread_id)
            limit = self._limits(db, run)["delegation_depth"]
            if nested and depth >= limit:
                self.write_tool_result(
                    thread_id,
                    call,
                    f"已达到委派深度上限（{limit} 层），请自己完成这一步，或把结论交回上级。",
                )
                return {}
            if nested:
                sup_id = supervisor_id(run, chat)
                if target.id == sup_id:
                    self.write_tool_result(
                        thread_id, call, "不能把任务派回总指挥，请把结论交回上级处理。"
                    )
                    return {}
                if target.id in self._ancestor_bot_ids(db, call_thread):
                    self.write_tool_result(
                        thread_id, call, "不能把任务派回自己的上级成员，请换一个成员或自己完成。"
                    )
                    return {}

            main = self._main_thread(db, chat)
            if not nested and run.kind != "free":
                plan = self._approved_plan_steps(db, run.id)
                matched = self._match_plan_step(plan, step_key)
                if plan and not matched and any("depends_on" in step for step in plan):
                    self.write_tool_result(thread_id, call,
                        "无法唯一匹配计划步骤，请在 step 中填写最终计划的完整标题或序号；新增工作请先调整计划。")
                    return {}
                if matched:
                    # 序号、简称和完整标题必须存成同一个键，供依赖、去重及改派计数共用。
                    step_key = _step_key(matched["title"])
                    expected = expected or matched.get("expected") or ""
                    latest = self._latest_plan_tasks(db, run.id, main.id)
                    blocked = self._blocked_dependencies(matched, latest)
                    if blocked:
                        self.write_tool_result(thread_id, call,
                            "前置依赖尚未完成，不能提前执行此步骤。请先完成或调整这些步骤：" + "、".join(blocked))
                        return {}
            # 节点落库后、检查点写入前重启时，复用同一调用已经创建的任务。
            existing = self._task_for_call(
                db, run.id, main.id, call.get("id"), thread_id=thread_id
            )
            if not existing:
                previous = db.scalar(select(Task).join(Thread, Thread.id == Task.thread_id).where(
                    Task.run_id == run.id, Task.step_key == step_key, Thread.parent_thread_id == thread_id,
                ).order_by(Task.created_at.desc()))
                active = previous and previous.status in ("queued", "running", "waiting_approval", "waiting_reply")
                unchanged = (previous and previous.status == "completed"
                    and previous.assigned_bot_id == target.id and previous.instruction == instruction
                    and (previous.expected_output or "") == expected)
                revision = bool(previous and previous.status == "completed" and not unchanged)
                retry = bool(previous and previous.status == "failed")
                if active or unchanged:
                    existing = previous
                    note = next((m for m in db.scalars(select(Message).where(
                        Message.run_id == run.id, Message.thread_id == main.id, Message.type == "text",
                    )) if (m.meta or {}).get("delegation") and (m.meta or {}).get("task_id") == existing.id), None)
                    if note and call.get("id"):
                        aliases = list((note.meta or {}).get("delegate_call_ids") or [])
                        if call["id"] not in aliases:
                            note.meta = {**(note.meta or {}), "delegate_call_ids": [*aliases, call["id"]]}
                            db.flush()
            if existing:
                if existing.status in ("completed", "failed", "canceled"):
                    self._answer_open_calls(db, call_thread or main, run.id,
                        self._delegate_summary(db, existing), call_id=call.get("id"))
                    return {}
                if existing.batch:
                    # 并行子执行还没回来：继续等，别重复建任务
                    deferred = True
                else:
                    return {"role": "worker", "bot_id": existing.assigned_bot_id,
                            "thread_id": existing.thread_id, "task_id": existing.id,
                            "last": existing.result or "", "has_calls": False, "cont": False}

            # 计划批次只属于总指挥：子 Bot 的嵌套派任务一律串行，避免子执行
            # 再拉起一批孙任务、打乱父执行的等待语义。
            # 已完成步骤的明确修订只执行该项，不重新拉起原并行批次。
            batch = None if (deferred or nested or revision or retry or run.kind == "free") else self._parallel_batch(db, run, chat, step_key)
            if batch:
                batch_no, batch_steps, matched_key = batch
                try:
                    child_ids, pending = self._start_parallel_batch(
                        db, run, chat, main, members, batch_no, batch_steps, matched_key,
                        call, args, bot_id,
                    )
                except ValueError as exc:
                    self.write_tool_result(thread_id, call, str(exc))
                    return {}
                if pending:
                    enqueue_ids = child_ids
                    deferred = True
                else:
                    # 这一批已经全部结束（或都无法派发）：给当前调用补结果，让总指挥继续
                    if not self._call_answered(db, main.id, run.id, call.get("id")):
                        self._answer_open_calls(
                            db, main, run.id,
                            "这一批并行步骤都已经结束或无法派发，请根据已有结果继续，不要重复派任务。",
                            call_id=call.get("id"),
                        )
                    return {}
            elif not deferred:
                # 同一子任务的判定用稳定键（RUN-10）：总指挥改派时换个说法、
                # 换个人，都不会再被当成新任务绕过改派上限。旧任务没有 step_key
                # 时按指令文本兜底，保证升级前后的执行口径一致。
                failed = [
                    t
                    for t in db.scalars(
                        select(Task).join(Thread, Thread.id == Task.thread_id).where(
                            Task.run_id == run.id, Task.status == "failed",
                            (Thread.parent_thread_id == thread_id) | (Task.thread_id == thread_id),
                        )
                    ).all()
                    if (t.step_key or _step_key(t.instruction)) == step_key
                ]
                if len(failed) > MAX_REASSIGN:
                    self.write_tool_result(thread_id, call, "同一子任务已达到改派次数上限，请向用户说明失败或跳过该步骤。")
                    return {}
                thread = Thread(
                    user_id=run.user_id,
                    chat_id=chat.id,
                    parent_thread_id=call_thread.id if nested else main.id,
                    type="task",
                    title=instruction[:60],
                    bot_id=target.id,
                )
                db.add(thread)
                db.flush()
                task = Task(
                    user_id=run.user_id,
                    run_id=run.id,
                    thread_id=thread.id,
                    assigned_bot_id=target.id,
                    instruction=instruction,
                    expected_output=expected,
                    attempt=len(failed) + 1,
                    step_key=step_key,
                    status="running",
                    started_at=now(),
                )
                db.add(task)
                db.flush()
                # 群聊里就是一条普通消息：总指挥 @ 目标 Bot，像人一样把活说清楚
                add_message(
                    db,
                    thread=main,
                    run_id=run.id,
                    sender_type="bot",
                    type_="text",
                    content=f"@{target.name} {instruction[:120]}" if run.kind == "free" else f"@{target.name} {instruction}",
                    sender_bot_id=bot_id,
                    meta={
                        "task_id": task.id,
                        "thread_id": thread.id,
                        "mention_bot_id": target.id,
                        "mention_name": target.name,
                        "expected_output": expected,
                        "delegation": True,
                        "delegation_instruction": instruction,
                        "tool_call_id": call.get("id"),
                        # 嵌套派任务：tool_call 在上级成员线程里，结果要回到那里；
                        # 主线程只保留这条 @ 展示消息（RUN-09）
                        "delegate_thread_id": thread_id,
                        "depth": depth + 1,
                        "nested": nested,
                        "peer_from": bot_id if run.kind == "free" and not nested else None,
                    },
                )
                # CHT-05 / RUN-08：任务卡片的结构化记录（前端按 compat 过滤不渲染）
                add_compat_message(
                    db,
                    thread=main,
                    run_id=run.id,
                    type_="task",
                    content=instruction,
                    sender_bot_id=bot_id,
                    meta={
                        "task_id": task.id,
                        "thread_id": thread.id,
                        "bot_id": target.id,
                        "bot_name": target.name,
                        "expected_output": expected,
                        "status": "running",
                        "attempt": task.attempt,
                        "tool_call_id": call.get("id"),
                    },
                )
                emit_task_status(db, task)
                log.info(
                    "派任务 task=%s 执行者=%s 第%s次 指令=%r",
                    task.id, target.name, task.attempt, instruction[:120],
                )
                patch = {
                    "role": "worker",
                    "bot_id": target.id,
                    "thread_id": thread.id,
                    "task_id": task.id,
                    "last": "",
                    "has_calls": False,
                    "cont": False,
                }
        for child_id in enqueue_ids:
            enqueue(child_id)
        if deferred:
            raise AwaitChildren()
        return patch
