"""Agent 编排：LangGraph 状态图的外壳。

控制流在 `app/graph.py`（StateGraph：model ⇄ tool → after），本模块只提供节点用到
的副作用实现：

    总指挥 / 子 Bot 共用一次模型调用（model_turn）
    ├─ propose_plan → 写计划卡片 → interrupt 暂停
    ├─ delegate     → 建子线程 + 任务 → 切换图里的角色为 worker
    ├─ ask_user     → 写问题卡片 → interrupt 暂停
    └─ 内置工具      → 按权限级别决定是否先审批（L2/L3）

暂停：`interrupt()` 的 payload 落库为 run.resume_payload；
用户处理后 API 把 run 重新入队，执行器用 `Command(resume=payload)` 从检查点继续。
所有中间状态都在 Postgres/SQLite 里（消息、任务、审批、检查点），进程重启不丢步骤。"""
from __future__ import annotations

import asyncio
import time
from sqlalchemy import select
from sqlalchemy.orm import Session
from app.agent import graph as graphmod
from app.agent import llm
from app.agent.messages import add_message
from app.tools import mcp as mcpmod
from app.tools import skills as skillmod
from app.tools import registry as toolmod
from app.core.db import session_scope
from app.core.events import emit
from app.agent.execution_config import execution_bot, execution_provider, supervisor_id
from app.agent.skill_usage import usage_prompt, usage_state
from app.agent.free_chat import FreeChatMixin
from app.core.redis_bus import enqueue
from app.agent.llm import LLMError, estimate_tokens, stream_chat
from app.core.models import Chat, Message, Run, Task, Thread, now
from app.core.security import decrypt_secret
from app.agent.runtime_helpers import (
    CHILD_ACTIVE_STATUSES,
    EMPTY_OUTPUT,
    ToolCtx,
    _cancel_child_task,
    _parse_json,
    _step_key,
    add_compat_message,
    emit_run_status,
    emit_task_status,
    log,
    settle_run_tasks,
    stop_child_runs,
)
from app.agent.signals import AwaitChildren, LeaseLost, LimitExceeded, StopSignal
from app.agent.delegation import DelegationMixin
from app.agent.task_completion import TaskCompletionMixin
from app.agent.tool_execution import ToolExecutionMixin
from app.agent.context_builder import ContextBuilderMixin
from app.agent.run_finish import RunFinishMixin
from app.agent.streaming import StreamingMixin
from app.agent.summary_memory import SummaryMemoryMixin

FREE_CHAT_FIRST_RESPONSE_TIMEOUT = 20.0
FREE_CHAT_REPLY_TIMEOUT = 30.0


class RunExecutor(
    DelegationMixin, TaskCompletionMixin, ToolExecutionMixin, ContextBuilderMixin,
    RunFinishMixin, SummaryMemoryMixin, StreamingMixin, FreeChatMixin,
):
    def __init__(self, run_id: str, worker_id: str | None = None, lease_token: int | None = None):
        self.run_id = run_id
        self.worker_id = worker_id
        self.lease_token = lease_token
        self._last_stop_check = 0.0
        self._last_flush = 0.0
        # MEM-04：候选生成是后台任务，保留引用避免被回收
        self._bg_tasks: set[asyncio.Task] = set()

    @staticmethod
    def _stream_model(**kwargs):
        return stream_chat(**kwargs)

    # ---------------- 入口 ----------------
    def _prepare_execution(self) -> tuple[dict, dict | None, str] | None:
        with session_scope() as db:
            run = db.get(Run, self.run_id)
            if not run or run.status in ("completed", "failed", "stopped"):
                return None
            chat = db.get(Chat, run.chat_id)
            if not chat:
                return None
            if not self._owns_lease(run):
                log.warning("执行租约已转移，拒绝启动旧 Worker run=%s", self.run_id)
                return None
            run.status = "running"
            if not run.started_at:
                run.started_at = now()
            if not run.parent_run_id and run.kind != "free":
                self._ensure_batch_summaries(db, run, chat)
            initial_task_id = run.parent_task_id or (run.config_snapshot or {}).get("retry_task_id")
            if initial_task_id:
                task = db.get(Task, initial_task_id)
                if task and task.status == "queued":
                    task.status = "running"
                    task.started_at = task.started_at or now()
                    emit_task_status(db, task)
            payload = run.resume_payload or None
            if payload and payload.get("task_id"):
                task = db.get(Task, payload["task_id"])
                if task and task.status in ("waiting_approval", "waiting_reply"):
                    task.status = "running"
                    emit_task_status(db, task)
            db.flush()
            emit_run_status(db, run)
            state = {"role": "chime"} if run.kind == "chime" else self._initial_state(db, run, chat)
            return state, payload, run.id

    def _wait_for_children(self, run_id: str) -> None:
        ready = False
        with session_scope() as db:
            run = db.get(Run, run_id)
            if not run or run.status in ("completed", "failed", "stopped") or not self._owns_lease(run):
                return
            run.status = "waiting_children"
            if self._free_ready(db, run):
                run.status = "queued"
                ready = True
            run.resume_payload = None
            run.lease_until = None
            run.worker_id = None
            db.flush()
            emit_run_status(db, run)
        if ready:
            enqueue(run_id)

    def _save_paused_state(self, run_id: str, paused: dict) -> None:
        with session_scope() as db:
            run = db.get(Run, run_id)
            if not run or run.status in ("completed", "failed", "stopped") or not self._owns_lease(run):
                return
            if run.status == "queued":
                log.info("暂停落盘跳过 run=%s：状态已是 queued，保留恢复方的决定", run_id)
                return
            run.status = "waiting"
            run.resume_payload = paused
            run.lease_until = None
            run.worker_id = None
            task = db.get(Task, paused.get("task_id")) if paused.get("task_id") else None
            if task and task.status == "running" and paused.get("kind") in ("approval", "question"):
                task.status = "waiting_approval" if paused["kind"] == "approval" else "waiting_reply"
                emit_task_status(db, task)
            db.flush()
            emit_run_status(db, run)

    async def execute(self) -> None:
        """一次执行：跑 LangGraph，直到结束或暂停。

        暂停点由 `interrupt()` 抛出，payload 落进 `runs.resume_payload`；
        用户处理完 API 把 run 重新入队，这里带着 `Command(resume=payload)` 从检查点继续。
        """
        prepared = await asyncio.to_thread(self._prepare_execution)
        if not prepared:
            return
        state, payload, run_id = prepared

        try:
            if state["role"] == "chime":
                await self.execute_chime()
                return
            paused = await graphmod.run_graph(self, state, payload)
        except LeaseLost:
            log.warning("执行租约已转移，停止旧 Worker run=%s", run_id)
            return
        except StopSignal:
            log.info("执行被用户停止 run=%s", run_id)
            await asyncio.to_thread(self._finish_stopped)
            return
        except AwaitChildren:
            # 总指挥已经派出这一批并行子任务，等它们全部返回再继续。
            # 这里不占用 Worker：执行转入 waiting_children，由子执行结束时唤醒。
            await asyncio.to_thread(self._wait_for_children, run_id)
            log.info("执行等待并行子任务 run=%s", run_id)
            return
        except LimitExceeded as exc:
            log.warning("执行触发上限 run=%s 原因=%s", run_id, exc.reason)
            await asyncio.to_thread(self._finish_limited, exc.reason)
            return
        except LLMError as exc:
            log.error("执行失败 run=%s 模型调用失败：%s", run_id, exc)
            await asyncio.to_thread(self._finish_failed, f"模型调用失败：{exc}")
            return
        except Exception as exc:  # noqa: BLE001
            log.exception("执行异常 run=%s", run_id)
            await asyncio.to_thread(self._finish_failed, f"执行异常：{type(exc).__name__}: {exc}")
            return

        if paused:
            await asyncio.to_thread(self._save_paused_state, run_id, paused)

    def _ensure_batch_summaries(self, db: Session, run: Run, chat: Chat) -> None:
        """给已经全部返回的计划批次补一条汇总（父执行恢复时调用，幂等）。"""
        rows = db.scalars(
            select(Task).where(Task.run_id == run.id, Task.batch > 0)
        ).all()
        if not rows:
            return
        main = self._main_thread(db, chat)
        for batch in sorted({t.batch for t in rows}):
            newest = max(
                (t for t in rows if t.batch == batch),
                key=lambda t: t.created_at,
            )
            self._maybe_write_batch_summary(db, main, newest)

    def is_child_run(self) -> bool:
        """当前执行是不是并行子任务（RUN-12）。"""
        with session_scope() as db:
            run = db.get(Run, self.run_id)
            return bool(run and run.parent_run_id)

    async def is_independent_task(self, task_id: str) -> bool:
        return await asyncio.to_thread(self._is_independent_task, task_id)

    def _is_independent_task(self, task_id: str) -> bool:
        with session_scope() as db:
            run = db.get(Run, self.run_id)
            return bool(run and task_id == (run.parent_task_id or (run.config_snapshot or {}).get("retry_task_id")))

    def _initial_state(self, db: Session, run: Run, chat: Chat) -> dict:
        if run.kind == "free":
            thread = self._main_thread(db, chat)
            run.thread_id = thread.id
            role = "free" if not run.parent_run_id else "peer"
            return {"role": role, "bot_id": "", "thread_id": thread.id,
                    "task_id": None, "done": False, "cont": False}
        main = self._main_thread(db, chat)
        # CHT-07：分支线程里的执行把上下文与回复都落在分支，不碰主线程
        thread = db.get(Thread, run.thread_id) if run.thread_id else main
        if not thread or thread.chat_id != chat.id:
            thread = main
        retry_task_id = (run.config_snapshot or {}).get("retry_task_id")
        if retry_task_id:
            task = db.get(Task, retry_task_id)
            if not task or task.run_id != run.id or task.thread_id != thread.id:
                raise LLMError("重试任务不存在或不属于当前执行")
            return {"role": "worker", "bot_id": task.assigned_bot_id, "thread_id": thread.id,
                    "task_id": task.id, "last": "", "has_calls": False, "cont": False, "done": False}
        # RUN-12：并行子执行固定以 worker 身份跑在自己的任务线程里
        if run.parent_task_id and run.target_bot_id:
            return {
                "role": "worker",
                "bot_id": run.target_bot_id,
                "thread_id": thread.id,
                "task_id": run.parent_task_id,
                "last": "",
                "has_calls": False,
                "cont": False,
                "done": False,
            }
        sup_id = supervisor_id(run, chat)
        if run.target_bot_id and run.target_bot_id != sup_id:
            role, bot_id = "worker", run.target_bot_id
        else:
            sup = execution_bot(db, run, sup_id) if sup_id else None
            if not sup:
                raise LLMError("群聊没有配置总指挥 Bot")
            role, bot_id = "supervisor", sup.id
        return {
            "role": role,
            "bot_id": bot_id,
            "thread_id": thread.id,
            "task_id": None,
            "last": "",
            "has_calls": False,
            "cont": False,
            "done": False,
        }

    # ---------------- 图节点：一次模型调用 ----------------
    def _replayed_turn(self, db: Session, run: Run, thread_id: str, role: str = "worker") -> tuple[bool, str] | None:
        """上一轮模型输出已经落库、但图还没消费：返回 (有工具调用, 文本)。

        「落库了但没消费」只可能出现在 DB 已提交、检查点还没写入时崩溃、图重放
        model 节点的窗口里：此时线程最后一条消息就是上一轮的产物。重放时直接
        复用那一轮的结论，避免再调一次模型（重复计步/计费）和重复消息。
        """
        free = (run.config_snapshot or {}).get("free") or {}
        turn = (free.get("selected") or {}).get("turn")
        if role == "peer" and free.get("pass_turn") == turn:
            return False, ""
        marker = run.last_turn_message_id
        if not marker:
            return None
        last = db.scalar(
            select(Message)
            .where(Message.run_id == run.id, Message.thread_id == thread_id)
            .order_by(Message.seq.desc())
            .limit(1)
        )
        if not last or last.id != marker:
            return None
        if role == "peer" and (last.meta or {}).get("free_turn") != turn:
            return None
        if last.type == "tool_call":
            # 工具调用还没执行：交给路由去 tool 节点，open call 会带着原来的
            # tool_call_id 继续，工具侧有自己的按 id 去重。
            return True, ""
        # 引导在上一轮输出期间到达：在原线程补一轮处理，不能把旧结论重放成最终结果。
        thread = db.get(Thread, thread_id)
        latest = max((m.seq for m in self._interjection_messages(db, run, thread)), default=0)
        if latest > (last.meta or {}).get("interjection_seq", 0):
            return None
        content = last.content or ""
        return False, "" if content == EMPTY_OUTPUT else content

    def _prepare_model_turn(self, *, role: str, bot_id: str, thread_id: str, task_id: str | None):
        is_supervisor = role == "supervisor"
        with session_scope() as db:
            run = db.get(Run, self.run_id)
            self._assert_lease(db, run)
            chat = db.get(Chat, run.chat_id)
            bot = execution_bot(db, run, bot_id)
            if not bot:
                raise LLMError("执行成员不存在，请重新选择群聊成员")
            thread = db.get(Thread, thread_id)

            replayed = self._replayed_turn(db, run, thread_id, role)
            if replayed is not None:
                return replayed
            # 上一轮流式写到一半进程就没了：草稿永远不会再被写完。空的直接删，
            # 有内容的标成「已中断」，避免前端留着永久“正在输入”的气泡。
            # 不限定线程：子 Bot 那轮可能在主线程还留了一条镜像草稿。
            for stale in db.scalars(
                select(Message).where(
                    Message.run_id == run.id,
                    Message.status == "streaming",
                )
            ).all():
                if not (stale.content or "").strip():
                    msg_id = stale.id
                    db.delete(stale)
                    db.flush()
                    emit(db, run.user_id, chat.id, "message.deleted", {"id": msg_id})
                    continue
                stale.status = "interrupted"
                db.flush()
                emit(db, run.user_id, chat.id, "message.completed", {
                    "message_id": stale.id,
                    "thread_id": stale.thread_id,
                    "content": stale.content,
                    "status": stale.status,
                    "meta": stale.meta or {},
                })

            provider = execution_provider(db, run, bot) if bot else None
            if not provider:
                raise LLMError(f"Bot「{bot.name}」未绑定可用的模型接入，请到设置页配置")
            model = bot.model or (
                provider.models[0]["name"] if provider and provider.models else "mock"
            )
            kind = provider.kind
            base_url = provider.base_url
            api_key = decrypt_secret(provider.api_key_encrypted)
            provider_id = provider.id
            members = self._member_bots(db, chat, run)
            can_delegate = self._can_delegate(db, run, thread_id, is_supervisor, task_id)
            # TOL-05 / 技能：Bot 自己的工具 + 已启用技能带来的工具，再与 MCP 工具合并
            allowed_tools = list(bot.tools or [])
            enabled_skills = skillmod.for_bot(db, run.user_id, bot)
            for name in skillmod.effective_tool_names(enabled_skills):
                if name not in allowed_tools:
                    allowed_tools.append(name)
            tool_list = toolmod.tools_for_bot(
                allowed_tools,
                is_supervisor,
                can_delegate=can_delegate,
                extra_tools=mcpmod.tools_for_user(db, run.user_id),
                can_consult_supervisor=thread.type == "task" and task_id is not None,
            )
            if role == "peer":
                tool_list = [t for t in tool_list if t.name not in ("propose_plan", "ask_user")]
            selected = (run.config_snapshot.get("free") or {}).get("selected") or {}
            observer = bool(role == "peer" and run.config_snapshot.get("free", {}).get("direct_response")
                            and selected and not selected.get("required"))
            if observer:
                tool_list = []
            # CHT-07：分支是「深入讨论」，不派任务、不出计划，避免打断主线程
            if thread.type == "branch":
                tool_list = [
                    t for t in tool_list if t.name not in ("delegate", "propose_plan")
                ]
            # MEM-05：主线程历史过长时，先把更早的消息压成摘要再构造上下文。
            # 摘要失败只告警、不影响本轮执行，仍会退回硬截断。
            interjections = self._interjection_messages(db, run, thread)
            interjection_seq = max((m.seq for m in interjections), default=0)
            messages = self._build_messages(
                db, run, chat, thread, bot, is_supervisor, members, interjections=interjections,
            )
            if enabled_skills and not observer:
                state = usage_state(db, run.id, thread_id, bot_id, enabled_skills)
                messages.append({"role": "system", "content": usage_prompt(enabled_skills, state)})
            # 只标记本轮实际读取的补充，模型调用期间到达的新消息仍留待下一轮。
            if is_supervisor and thread.type == "main":
                self._mark_interjections_read(db, run, thread, seq=interjection_seq)
            temperature = bot.temperature if bot.temperature is not None else 0.4
            plan_approved = self._plan_already(db, thread_id)
            task = db.get(Task, task_id) if task_id else None
            task_instruction = task.instruction if task else ""
            bot_name = bot.name
            # 子 Bot 在任务线程里工作，但群聊要能看到它说话：把同一轮文本
            # 同步成主线程的镜像消息。工具调用仍只留在任务线程。
            mirror_thread_id = None
            if thread.type == "task" and task_id and run.kind != "free":
                mirror_thread_id = self._main_thread(db, chat).id
            # 步数不再在开跑前加：这一轮真正落库时才计一步（见 _finalize_message
            # 与下面的工具轮），否则崩溃重放会把同一轮算两次。

        return {
            "kind": kind, "base_url": base_url, "api_key": api_key, "model": model,
            "provider_id": provider_id, "tool_list": tool_list, "messages": messages,
            "interjection_seq": interjection_seq, "temperature": temperature,
            "task_instruction": task_instruction, "bot_name": bot_name,
            "member_names": [member.name for member in members if member.id != bot_id],
            "plan_approved": plan_approved, "mirror_thread_id": mirror_thread_id,
            "peer_selected": (run.config_snapshot.get("free") or {}).get("selected") if role == "peer" else None,
            "observer": observer,
            "trigger_message_id": run.trigger_message_id,
        }

    async def model_turn(
        self, *, role: str, bot_id: str, thread_id: str, task_id: str | None
    ) -> tuple[bool, str]:
        """跑一轮模型。返回 (是否有工具调用, 本轮文本)。"""
        is_supervisor = role == "supervisor"
        await asyncio.to_thread(self._check_stop)
        await asyncio.to_thread(self._check_limits)
        if is_supervisor:
            summary_config = await asyncio.to_thread(self._summary_model_config, bot_id, thread_id)
            if summary_config:
                await self._maybe_summarize(thread_id, self.run_id, *summary_config)

        prepared = await asyncio.to_thread(
            self._prepare_model_turn, role=role, bot_id=bot_id,
            thread_id=thread_id, task_id=task_id,
        )
        if isinstance(prepared, tuple):
            return prepared
        kind = prepared["kind"]
        base_url = prepared["base_url"]
        api_key = prepared["api_key"]
        model = prepared["model"]
        provider_id = prepared["provider_id"]
        tool_list = prepared["tool_list"]
        messages = prepared["messages"]
        interjection_seq = prepared["interjection_seq"]
        temperature = prepared["temperature"]
        task_instruction = prepared["task_instruction"]
        bot_name = prepared["bot_name"]
        member_names = prepared["member_names"]
        plan_approved = prepared["plan_approved"]
        mirror_thread_id = prepared["mirror_thread_id"]

        peer = prepared["peer_selected"]
        draft = await asyncio.to_thread(self._open_stream_message, thread_id, bot_id,
                                      meta={"free_turn": peer["turn"], "parent_run_id": peer.get("root_run_id"),
                                            "trigger_message_id": prepared["trigger_message_id"],
                                            "reply_to": peer.get("handoff_message_id") or peer.get("reply_to_message_id") or prepared["trigger_message_id"],
                                            "free_observer": prepared["observer"]} if peer else None)
        mirror = None
        if mirror_thread_id:
            mirror = await asyncio.to_thread(
                self._open_stream_message,
                mirror_thread_id,
                bot_id,
                meta={"task_id": task_id, "subtask_live": True},
            )
        drafts = [draft] + ([mirror] if mirror else [])

        result = None
        buffer = ""
        started = time.time()
        ctx = {
            "role": role,
            "bot_name": bot_name,
            "members": member_names,
            "task": task_instruction,
            "plan_approved": plan_approved,
            "observer": prepared["observer"],
            "required_reply": bool(peer and peer["required"]),
        }
        publication_allowed = None
        declined = False

        async def flush_text():
            nonlocal publication_allowed
            if prepared["observer"]:
                return  # 简短补充先完整生成，确认没有复述前文后再发布。
            if peer and "[PASS]".startswith(buffer.strip()):
                return
            if peer and publication_allowed is None:
                publication_allowed = await asyncio.to_thread(self._claim_free_speaker)
            if publication_allowed is not False:
                await self._flush_delta(drafts, buffer, force=False)

        watched = self._watch_model_stream(stream_chat(
            kind=kind, base_url=base_url, api_key=api_key, model=model, messages=messages,
            tools=toolmod.schema_list(tool_list), temperature=temperature, ctx=ctx,
            **({"max_output_tokens": 256, "max_retries": 0} if prepared["observer"] else
               {"max_retries": 0} if peer and peer.get("primary") else {}),
        ), on_idle=flush_text,
            first_response_timeout=FREE_CHAT_FIRST_RESPONSE_TIMEOUT if peer and (
                prepared["observer"] or peer.get("primary")) else None,
            total_timeout=FREE_CHAT_REPLY_TIMEOUT if prepared["observer"] else None)
        try:
            async for chunk in watched:
                if chunk["type"] == "delta":
                    buffer += chunk["text"]
                    await flush_text()
                    if publication_allowed is False:
                        declined = True
                        result = {"content": "", "tool_calls": [], "usage": {
                            "input_tokens": estimate_tokens(str(messages)), "output_tokens": estimate_tokens(buffer)}}
                        break
                    await self._check_stop_throttled()
                elif chunk["type"] == "done":
                    result = chunk
        except LLMError as exc:
            if buffer.strip() and (not peer or publication_allowed is True):
                for d in drafts:
                    await asyncio.to_thread(self._finalize_message, d, buffer, status="interrupted")
            else:
                for d in drafts:
                    await asyncio.to_thread(self._drop_message, d["id"])
            await self._record_usage_async(
                run_id=self.run_id,
                task_id=task_id,
                bot_id=bot_id,
                provider_id=provider_id,
                model=model,
                usage={
                    "input_tokens": estimate_tokens(str(messages)),
                    "output_tokens": estimate_tokens(buffer),
                },
                latency_ms=int((time.time() - started) * 1000),
                error=str(exc),
            )
            raise
        finally:
            await watched.aclose()

        assert result is not None
        latency = int((time.time() - started) * 1000)
        usage = result.get("usage") or {}
        log.info(
            "模型调用 role=%s bot=%s model=%s latency=%dms in=%s out=%s tool_calls=%d",
            role,
            bot_name,
            model,
            latency,
            usage.get("input_tokens"),
            usage.get("output_tokens"),
            len(result.get("tool_calls") or []),
        )
        agent_tokens = await self._record_usage_async(
            run_id=self.run_id,
            task_id=task_id,
            bot_id=bot_id,
            provider_id=provider_id,
            model=model,
            usage=result.get("usage") or {},
            latency_ms=latency,
        )

        content = result.get("content") or buffer
        reasoning = result.get("reasoning_content") or ""
        calls = result.get("tool_calls") or []
        if prepared["observer"]:
            calls = []
        usage_meta = {"interjection_seq": interjection_seq}
        if peer:
            usage_meta["free_turn"] = peer["turn"]
        if task_id is None:
            usage_meta["agent_tokens"] = agent_tokens

        if peer and declined:
            await asyncio.to_thread(self._mark_peer_pass, draft["id"])
            return False, ""
        if not calls:
            if peer and (not content.strip() or content.strip() == "[PASS]"):
                if peer["required"]:
                    await asyncio.to_thread(self._drop_message, draft["id"])
                    raise LLMError("成员未返回有效回应")
                else:
                    await asyncio.to_thread(self._mark_peer_pass, draft["id"])
                    return False, ""
            if prepared["observer"] and await asyncio.to_thread(self._duplicate_free_reply, content):
                await asyncio.to_thread(self._mark_peer_pass, draft["id"])
                return False, ""
            if peer:
                publication_allowed = await asyncio.to_thread(self._claim_free_speaker, content)
            if peer and not publication_allowed:
                await asyncio.to_thread(self._mark_peer_pass, draft["id"])
                return False, ""
            final = content or EMPTY_OUTPUT
            await asyncio.to_thread(self._finalize_message,
                draft, final, status="done", reasoning=reasoning, mark_turn=True, meta=usage_meta,
            )
            if mirror:
                await asyncio.to_thread(self._finalize_message,
                    mirror,
                    final,
                    status="done",
                    reasoning=reasoning,
                    meta={"task_id": task_id, "subtask_live": True},
                )
            return False, content

        if peer and publication_allowed is None:
            publication_allowed = await asyncio.to_thread(self._claim_free_speaker)
        if peer and not publication_allowed:
            await asyncio.to_thread(self._mark_peer_pass, draft["id"])
            return False, ""
        # 有工具调用：把流式草稿转成 tool_call 消息（每个调用一条，保证可重放）
        for idx, call in enumerate(calls):
            if idx == 0:
                # 原位收尾，保留已输出文字的消息 id，避免气泡消失后另建一条。
                await asyncio.to_thread(self._finalize_message,
                    draft, content, reasoning=reasoning, type_="tool_call",
                    meta={"tool_calls": [call], **usage_meta}, mark_turn=len(calls) == 1,
                )
                continue
            await asyncio.to_thread(
                self._save_tool_call_record, thread_id, bot_id, call, usage_meta,
                reasoning, idx == len(calls) - 1,
            )
        if mirror:
            # 工具轮的文字是给群聊看的“过程说明”；没有文字就不留空气泡
            if content.strip():
                await asyncio.to_thread(self._finalize_message,
                    mirror,
                    content,
                    status="done",
                    reasoning=reasoning,
                    meta={"task_id": task_id, "subtask_live": True},
                )
            else:
                await asyncio.to_thread(self._drop_message, mirror["id"])
        return True, content

    def _save_tool_call_record(
        self, thread_id: str, bot_id: str, call: dict, usage_meta: dict,
        reasoning: str, last: bool,
    ) -> None:
        with session_scope() as db:
            self._assert_lease(db)
            thread = db.get(Thread, thread_id)
            msg = add_message(
                db, thread=thread, run_id=self.run_id, sender_type="bot", type_="tool_call",
                sender_bot_id=bot_id, meta={"tool_calls": [call], **usage_meta},
            )
            msg.reasoning_content = reasoning
            db.flush()
            if last:
                run = db.get(Run, self.run_id)
                if run:
                    run.last_turn_message_id = msg.id
                    run.steps_used += 1

    async def _watch_model_stream(self, stream, on_idle=None, *, first_response_timeout=None, total_timeout=None):
        """无 token 返回、限流等待期间也响应停止和状态时长限制。"""
        pending = None
        last_limits = 0.0
        started = time.monotonic()
        received_text = False
        try:
            while True:
                pending = asyncio.create_task(anext(stream))
                while not pending.done():
                    await asyncio.wait({pending}, timeout=0.25)
                    elapsed = time.monotonic() - started
                    if ((first_response_timeout is not None and not received_text and elapsed >= first_response_timeout)
                            or (total_timeout is not None and elapsed >= total_timeout)):
                        raise LLMError("自由聊天回应等待超时，已让其他成员继续接话", retryable=False)
                    if on_idle:
                        await on_idle()
                    await self._check_stop_throttled()
                    if time.monotonic() - last_limits >= 1:
                        await asyncio.to_thread(self._check_limits)
                        last_limits = time.monotonic()
                try:
                    chunk = pending.result()
                except StopAsyncIteration:
                    return
                if chunk.get("type") == "delta" and chunk.get("text", "").strip():
                    received_text = True
                yield chunk
        finally:
            if pending:
                if not pending.done():
                    pending.cancel()
                await asyncio.gather(pending, return_exceptions=True)
            await stream.aclose()

    # ---------------- 图节点：工具 ----------------
    def next_open_call(self, thread_id: str) -> Message | None:
        """本执行在当前线程里最早一条还没有结果的工具调用。"""
        with session_scope() as db:
            rows = self._open_tool_calls(db, thread_id, self.run_id)
            return rows[0] if rows else None

    def begin_tool(self, message_id: str) -> None:
        """每个实际工具调用计一步；审批恢复/节点重放不能重复计数。"""
        self._check_stop()
        with session_scope() as db:
            self._assert_lease(db)
            msg = db.get(Message, message_id)
            counted = bool((msg.meta or {}).get("step_counted"))
            self._check_limits()
            if not counted:
                msg.meta = {**(msg.meta or {}), "step_counted": True}
                db.get(Run, self.run_id).steps_used += 1


    # ---------------- 图节点：派任务 / 收尾 ----------------


        # MEM-04 的候选提取只在执行状态成功提交后调度。


    # ---------------- 工具分发 ----------------


    # ---------------- 消息构造 ----------------


    # ---------------- RUN-09：委派深度与嵌套派任务 ----------------


    async def _complete_text(
        self,
        *,
        kind: str,
        base_url: str,
        api_key: str | None,
        model: str,
        messages: list[dict],
        temperature: float = 0.3,
    ) -> str:
        """消费一次模型输出，只取正文（用于摘要等维护性调用，不落业务消息）。

        走 `llm.stream_chat` 而不是模块级 `stream_chat`：维护性调用不应被业务流
        的 monkeypatch/包装拦截，也不会混进「本轮模型调用」的统计。
        """
        text = ""
        async for chunk in llm.stream_chat(
            kind=kind,
            base_url=base_url,
            api_key=api_key or "",
            model=model,
            messages=messages,
            tools=None,
            temperature=temperature,
        ):
            if chunk["type"] == "done":
                text = chunk.get("content") or text
        return (text or "").strip()


    # ---------------- 用量与限额 ----------------


    # ---------------- 收尾 ----------------


async def execute_run(run_id: str, worker_id: str | None = None, lease_token: int | None = None) -> None:
    await RunExecutor(run_id, worker_id, lease_token).execute()
