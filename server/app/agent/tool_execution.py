"""工具执行、计划、提问与审批。"""
from __future__ import annotations

import asyncio
import json
import time
from datetime import timedelta
from sqlalchemy import select
from app.agent.messages import add_message
from app.tools import mcp as mcpmod
from app.sandbox import files as sandbox
from app.tools import skills as skillmod
from app.tools import registry as toolmod
from app.core.config import APPROVAL_TTL_HOURS, MCP_TIMEOUT
from app.core.db import session_scope
from app.core.events import emit, notify
from app.agent.execution_config import execution_bot, execution_provider, supervisor_id
from app.agent.llm import LLMError, estimate_tokens
from app.core.logging_setup import task_id_var
from app.agent.scheduling import schedule_plan
from app.agent.skill_usage import call_source
from app.core.models import Approval, Chat, McpServer, Message, Question, Run, Task, Thread, UserSettings, now
from app.core.security import args_hash, decrypt_secret
from app.agent.signals import PauseSignal
from app.tools.registry import ToolError
from app.agent.runtime_helpers import (
    ToolCtx,
    _parse_json,
    log,
)


class ToolExecutionMixin:
    async def execute_tool(
        self,
        *,
        thread_id: str,
        bot_id: str,
        task_id: str | None,
        call: dict,
        role: str,
    ) -> str:
        await asyncio.to_thread(self._check_stop)
        name = (call.get("function") or {}).get("name", "?")
        started = time.time()
        token = task_id_var.set(task_id or "-")
        try:
            result = await self._handle_tool_call(
                thread_id=thread_id,
                bot_id=bot_id,
                task_id=task_id,
                call=call,
                is_supervisor=(role == "supervisor"),
            )
        except PauseSignal as sig:
            # 正常暂停点（计划确认 / 审批 / 提问 / 逐步确认）不是异常：
            # 只记一条 INFO，不打 Traceback，否则每次等用户确认都会刷一条假 ERROR。
            log.info("工具调用暂停 tool=%s role=%s kind=%s，等待用户确认", name, role, sig.payload.get("kind"))
            raise
        except Exception:
            log.exception("工具调用异常 tool=%s role=%s", name, role)
            raise
        finally:
            task_id_var.reset(token)
        log.info(
            "工具调用 tool=%s role=%s %dms 结果=%d 字",
            name, role, int((time.time() - started) * 1000), len(result or ""),
        )
        return result

    def write_tool_result(self, thread_id: str, call: dict, content: str) -> None:
        """记录工具结果。

        只写进执行所在的线程（子任务线程 / 主线程）。群聊保持「只有聊天记录」：
        子 Bot 的工具调用与返回不在主线程再镜像一份，需要看过程时进任务面板。
        """
        with session_scope() as db:
            self._assert_lease(db)
            existing = db.scalar(select(Message).where(Message.thread_id == thread_id, Message.run_id == self.run_id,
                                 Message.type == "tool_result", Message.meta["tool_call_id"].as_string() == call.get("id")))
            if existing:
                return
            thread = db.get(Thread, thread_id)
            tool_name = (call.get("function") or {}).get("name")
            add_message(
                db,
                thread=thread,
                run_id=self.run_id,
                sender_type="system",
                type_="tool_result",
                content=content,
                meta={"tool_call_id": call.get("id"), "tool": tool_name},
            )

    def answer_open_calls(self, content: str) -> None:
        """给悬空的工具调用补一条结果（僵尸执行回收时用）。"""
        with session_scope() as db:
            run = db.get(Run, self.run_id)
            self._assert_lease(db, run)
            chat = db.get(Chat, run.chat_id)
            main = self._main_thread(db, chat)
            self._answer_open_calls(db, main, run.id, content)

    def _load_supervisor_consultation(self, *, thread_id: str, bot_id: str, task_id: str, call: dict, args: dict):
        with session_scope() as db:
            run = db.get(Run, self.run_id)
            self._assert_lease(db, run)
            task = db.get(Task, task_id)
            if (not run or not task or task.user_id != run.user_id or task.thread_id != thread_id
                    or task.assigned_bot_id != bot_id or task.run_id not in (run.id, run.parent_run_id)):
                return "无法澄清：该任务不属于当前执行成员或线程。"
            existing = db.scalar(select(Message).where(
                Message.run_id == run.id, Message.thread_id == thread_id, Message.type == "tool_result",
                Message.meta["tool_call_id"].as_string() == call.get("id"),
            ))
            if existing:
                return existing.content
            root = db.get(Run, run.parent_run_id) if run.parent_run_id else run
            chat = db.get(Chat, run.chat_id)
            sid = supervisor_id(root, chat)
            supervisor = execution_bot(db, root, sid) if sid else None
            provider = execution_provider(db, root, supervisor) if supervisor else None
            if not supervisor or not provider:
                return "总指挥未配置可用模型，暂时无法澄清；缺少用户决定的信息时请调用 ask_user。"
            thread = self._main_thread(db, chat)
            system = self._build_system_prompt(db, root, chat, supervisor, True,
                                               self._member_bots(db, chat, root), thread=thread)
            trigger = db.get(Message, root.trigger_message_id) if root.trigger_message_id else None
            corrections = [m.content for m in db.scalars(select(Message).where(
                Message.thread_id == thread.id, Message.run_id == root.id, Message.sender_type == "user",
            ).order_by(Message.seq)) if (m.meta or {}).get("kind") == "interjection"]
            completed = [{"goal": t.instruction, "result": t.result} for t in db.scalars(select(Task).where(
                Task.run_id == root.id, Task.status == "completed",
            )) if t.result]
            messages = [
                {"role": "system", "content": system + "\n\n## 子 Agent 任务澄清\n"
                 "现在只回答子 Agent 的疑问，不派新任务、不执行专业工作。"
                 "依据用户原始目标、补充约束和已有结果澄清范围、依赖、验收口径；"
                 "具体执行方法、工具、技能和操作步骤仍由子 Agent 自主选择，不代替它设计方案。"
                 "不要虚构用户偏好；如果现有信息不能决定，明确说明需向用户确认什么。"},
                {"role": "user", "content": json.dumps({
                    "original_request": trigger.content if trigger else "",
                    "user_corrections": corrections,
                    "task_goal": task.instruction, "expected_output": task.expected_output,
                    "completed_results": completed,
                    "worker_question": args["question"], "worker_context": args.get("context") or "",
                }, ensure_ascii=False)},
            ]
            root_id, supervisor_id_value, supervisor_name = root.id, supervisor.id, supervisor.name
            kind, base_url, provider_id = provider.kind, provider.base_url, provider.id
            api_key = decrypt_secret(provider.api_key_encrypted)
            model = supervisor.model or (provider.models[0]["name"] if provider.models else "mock")
            temperature = supervisor.temperature if supervisor.temperature is not None else 0.4

        return (messages, root_id, supervisor_id_value, supervisor_name, kind, base_url, provider_id, api_key, model, temperature)

    async def _consult_supervisor(
        self, *, thread_id: str, bot_id: str, task_id: str, call: dict, args: dict,
    ) -> str:
        """子 Agent 就任务目标向总指挥提问，答复作为工具结果交回原线程。"""
        prepared = await asyncio.to_thread(
            self._load_supervisor_consultation,
            thread_id=thread_id, bot_id=bot_id, task_id=task_id, call=call, args=args,
        )
        if isinstance(prepared, str):
            return prepared
        (messages, root_id, supervisor_id_value, supervisor_name, kind, base_url,
         provider_id, api_key, model, temperature) = prepared

        started = time.monotonic()
        result = None
        buffer = ""
        try:
            async for chunk in self._watch_model_stream(self._stream_model(
                kind=kind, base_url=base_url, api_key=api_key, model=model, messages=messages,
                tools=None, temperature=temperature,
                ctx={"role": "supervisor", "bot_name": supervisor_name, "consultation": True,
                     "task": args["question"]},
            )):
                if chunk["type"] == "delta":
                    buffer += chunk["text"]
                elif chunk["type"] == "done":
                    result = chunk
        except LLMError as exc:
            await self._record_usage_async(
                run_id=root_id, task_id=None, bot_id=supervisor_id_value, provider_id=provider_id, model=model,
                usage={"input_tokens": estimate_tokens(str(messages)), "output_tokens": estimate_tokens(buffer)},
                latency_ms=int((time.monotonic() - started) * 1000), error=str(exc),
            )
            return f"总指挥暂时未能答复：{exc}。不要猜测缺失的用户决定，必要时调用 ask_user。"
        await self._record_usage_async(
            run_id=root_id, task_id=None, bot_id=supervisor_id_value, provider_id=provider_id, model=model,
            usage=(result or {}).get("usage") or {}, latency_ms=int((time.monotonic() - started) * 1000),
        )
        answer = (result or {}).get("content") or buffer
        if not answer.strip():
            return "总指挥没有返回有效澄清；缺少用户决定的信息时请调用 ask_user。"
        reply = f"总指挥「{supervisor_name}」的任务澄清：\n\n{answer}\n\n具体执行方法、工具和技能仍由你自主决定。"
        # 先保存答复，图节点重放时复用，不重复询问总指挥或重复计费。
        await asyncio.to_thread(self.write_tool_result, thread_id, call, reply)
        return reply

    def _propose_plan_db(self, steps: list[dict], bot_id: str, call: dict) -> tuple[str | None, list[dict]]:
        with session_scope() as db:
            self._assert_lease(db)
            run = db.get(Run, self.run_id)
            chat = db.get(Chat, run.chat_id)
            main = self._main_thread(db, chat)
            old = self._find_card(db, run.id, "plan", call.get("id"))
            if old:
                if (old.meta or {}).get("status") == "canceled":
                    return "用户取消了这次计划，请停下来询问用户接下来的安排。", steps
                steps = (old.meta or {}).get("steps") or steps
                return (
                    "用户已确认以下计划，请以这些最终步骤为准派任务：同一个 batch 的步骤"
                    "由执行器根据你判断的 depends_on 自动安排；同批可以先派一项，系统会补齐已就绪的未启动项并行。"
                    "每步等待其前置依赖最新一次执行成功，无关步骤失败不阻塞已就绪工作；重试只派具体步骤。"
                    "delegate 的 step 参数原样填最终步骤标题，指令中带上相关前置结果和产物路径。拿到结果后"
                    "再汇总，不要只复述计划。\n" + json.dumps(steps, ensure_ascii=False), steps,
                )
            bot = execution_bot(db, run, bot_id)
            provider = execution_provider(db, run, bot) if bot else None
            if provider and provider.kind != "mock" and any("depends_on" not in step for step in steps):
                return (
                    "请自主判断每一步的前置依赖，并重新提交计划。每步都必须填写 depends_on："
                    "没有依赖填写 []，有依赖填写前置步骤标题。不要询问用户选择并行或串行。", steps,
                )
            try:
                steps = schedule_plan(steps)
            except ValueError as exc:
                return f"计划依赖不合法：{exc}。请自主修正后重新提交。", steps
            approved = any((m.meta or {}).get("status") == "approved" for m in db.scalars(
                select(Message).where(Message.run_id == run.id, Message.type == "plan")))
            if approved:
                notice = self._find_card(db, run.id, "system", call.get("id"))
                if not notice:
                    text = "后续计划已调整：\n" + "\n".join(
                        f"{i + 1}. {step['bot']}：{step['title']}" for i, step in enumerate(steps)
                    )
                    add_message(db, thread=main, run_id=run.id, sender_type="system", type_="system",
                                content=text, meta={"kind": "plan_adjusted", "steps": steps,
                                                    "tool_call_id": call.get("id")})
                return (
                    "已记录后续计划调整，请根据已完成的任务继续执行，不要重复已完成的工作。\n"
                    + json.dumps(steps, ensure_ascii=False), steps,
                )
            add_message(
                db, thread=main, run_id=run.id, sender_type="bot", type_="plan",
                content="我建议按以下步骤执行：", sender_bot_id=bot_id,
                meta={"steps": steps, "status": "pending", "tool_call_id": call.get("id")},
            )
            notify(db, run.user_id, "plan", f"「{chat.name}」的计划等待确认", "确认后才会开始派任务",
                   chat_id=run.chat_id, target_type="run", target_id=run.id)
            db.flush()
            return None, steps

    def _ask_user_db(self, question: str, bot_id: str, task_id: str | None,
                     thread_id: str, call: dict) -> tuple[str | None, str | None]:
        with session_scope() as db:
            self._assert_lease(db)
            run = db.get(Run, self.run_id)
            chat = db.get(Chat, run.chat_id)
            main = self._main_thread(db, chat)
            bot = execution_bot(db, run, bot_id)
            old = self._find_card(db, run.id, "question", call.get("id"))
            if old:
                q = db.get(Question, (old.meta or {}).get("question_id"))
                if q and q.status == "answered":
                    return f"用户回复：{q.answer}", None
                return "用户还没有回复这个问题。", None
            q = Question(
                user_id=run.user_id, run_id=run.id, task_id=task_id, bot_id=bot_id,
                question=question, expires_at=now() + timedelta(hours=APPROVAL_TTL_HOURS),
            )
            db.add(q)
            db.flush()
            add_message(db, thread=main, run_id=run.id, sender_type="bot", type_="question",
                        content=question, sender_bot_id=bot_id,
                        meta={"question_id": q.id, "bot_name": bot.name if bot else "",
                              "status": "pending", "tool_call_id": call.get("id")})
            notify(db, run.user_id, "question", f"{bot.name if bot else 'Bot'} 在等你回复",
                   question[:120], chat_id=run.chat_id, target_type="question", target_id=q.id)
            db.flush()
            emit(db, run.user_id, run.chat_id, "question.created",
                 {"question_id": q.id, "run_id": run.id, "bot_id": bot_id})
            return None, q.id

    async def _handle_tool_call(
        self,
        *,
        thread_id: str,
        bot_id: str,
        task_id: str | None,
        call: dict,
        is_supervisor: bool,
    ) -> str:
        name = (call.get("function") or {}).get("name") or ""
        raw_args = (call.get("function") or {}).get("arguments") or "{}"
        args = _parse_json(raw_args)
        control = {"propose_plan": toolmod.PLAN_TOOL, "ask_user": toolmod.ASK_TOOL,
                   "ask_supervisor": toolmod.ASK_SUPERVISOR_TOOL}.get(name)
        if control:
            error = toolmod.validate_args(control, args)
            if error:
                return f"参数不合法：{error}"
        if name in ("propose_plan", "ask_user") and not task_id:
            with session_scope() as db:
                run = db.get(Run, self.run_id)
                card_type = "plan" if name == "propose_plan" else "question"
                if run.kind == "free" and not self._find_card(db, run.id, card_type, call.get("id")):
                    return "自由群聊中请直接回应或用聊天文字提问；需要正式计划审批时请用户切换到任务模式。"
        if name == "propose_plan" and not is_supervisor:
            with session_scope() as db:
                run = db.get(Run, self.run_id)
                leader = (run.config_snapshot.get("supervisor") or {}).get("id")
                if run.kind != "free" or bot_id != leader or task_id:
                    return "只有总指挥或自由群聊负责人可以提交执行计划。"
        if name == "ask_supervisor":
            if is_supervisor or not task_id:
                return "只有正在执行子任务的成员可以向总指挥澄清任务。"
            return await self._consult_supervisor(
                thread_id=thread_id, bot_id=bot_id, task_id=task_id, call=call, args=args,
            )

        if name == "propose_plan":
            steps = args.get("steps") or []
            if not steps:
                return "计划为空，请直接回答或重新提交计划"
            response, steps = await asyncio.to_thread(self._propose_plan_db, steps, bot_id, call)
            if response:
                return response
            raise PauseSignal({
                "kind": "plan", "phase": "supervisor", "thread_id": thread_id,
                "bot_id": bot_id, "plan": steps, "tool_call": call,
            })

        if name == "ask_user":
            question = args.get("question") or "需要你确认一下"
            response, qid = await asyncio.to_thread(
                self._ask_user_db, question, bot_id, task_id, thread_id, call
            )
            if response:
                return response
            raise PauseSignal({
                "kind": "question", "phase": "task" if task_id else "supervisor",
                "task_id": task_id, "thread_id": thread_id, "bot_id": bot_id,
                "question_id": qid, "tool_call": call,
            })

        if name == "delegate":
            # 正常路径由图的 tool 节点处理（含 RUN-09 的嵌套派任务），这里只兜底
            return "派任务由编排层处理，不能作为普通工具直接执行。"

        with session_scope() as db:
            run = db.get(Run, self.run_id)
            bot = execution_bot(db, run, bot_id)
            allowed = set(bot.tools or []) if bot else set()
            if bot:
                allowed.update(skillmod.effective_tool_names(skillmod.for_bot(db, run.user_id, bot)))
            if not bot or name not in allowed:
                return f"该 Bot 未获授权使用工具：{name}"
            tool = toolmod.BUILTIN_TOOLS.get(name) or mcpmod.find_tool(db, run.user_id, name)
            if not tool:
                return f"未知工具：{name}"

        err = toolmod.validate_args(tool, args)
        if err:
            return f"参数不合法：{err}"

        # 审批闸门
        gate = await self._approval_gate(
            thread_id=thread_id,
            bot_id=bot_id,
            task_id=task_id,
            call=call,
            tool=tool,
            args=args,
        )
        if gate is not None:
            return gate

        if tool.kind == "mcp":
            return await self._execute_mcp(tool, args, thread_id=thread_id, call=call)
        return await self._execute_builtin(tool, args, bot_id, task_id, thread_id=thread_id, call=call)

    async def _approval_gate(
        self,
        *,
        thread_id: str,
        bot_id: str,
        task_id: str | None,
        call: dict,
        tool: toolmod.Tool,
        args: dict,
    ) -> str | None:
        return await asyncio.to_thread(
            self._approval_gate_sync,
            thread_id=thread_id, bot_id=bot_id, task_id=task_id,
            call=call, tool=tool, args=args,
        )

    def _approval_gate_sync(
        self,
        *,
        thread_id: str,
        bot_id: str,
        task_id: str | None,
        call: dict,
        tool: toolmod.Tool,
        args: dict,
    ) -> str | None:
        with session_scope() as db:
            self._assert_lease(db)
            run = db.get(Run, self.run_id)
            chat = db.get(Chat, run.chat_id)
            needs = tool.level == "L3" or (
                tool.level == "L2" and bool(chat.require_approval_for_write)
            )
            if tool.name in (chat.always_allow_tools or []):
                needs = False
            if not needs:
                return None

            h = args_hash(args)
            existing = db.scalar(
                select(Approval)
                .where(
                    Approval.run_id == run.id,
                    Approval.tool == tool.name,
                    Approval.args_hash == h,
                )
                .order_by(Approval.created_at.desc())
            )
            if existing:
                card = self._find_card(db, run.id, "approval", call.get("id"))
                replay = card and (card.meta or {}).get("approval_id") == existing.id
                if existing.status == "approved" and (existing.consumed_at is None or replay):
                    existing.consumed_at = existing.consumed_at or now()
                    db.flush()
                    return None  # 放行，由调用方执行
                if existing.status == "rejected":
                    reason = existing.reject_reason or "用户拒绝"
                    return f"用户拒绝执行 {tool.name}。原因：{reason}。请据此调整方案，不要重复同一调用。"
                if existing.status == "pending":
                    raise PauseSignal(
                        {
                            "kind": "approval",
                            "phase": "task" if task_id else "supervisor",
                            "task_id": task_id,
                            "thread_id": thread_id,
                            "bot_id": bot_id,
                            "approval_id": existing.id,
                            "tool_call": call,
                        }
                    )
                if existing.status == "expired":
                    return f"审批已过期，{tool.name} 未执行。请改用其他方式或告知用户。"

            settings = db.get(UserSettings, run.user_id)
            ttl = settings.approval_ttl_hours if settings else APPROVAL_TTL_HOURS
            bot = execution_bot(db, run, bot_id)
            source = next((m for m in self._open_tool_calls(db, thread_id, run.id)
                           if any(c.get("id") == call.get("id") for c in (m.meta or {}).get("tool_calls", []))), None)
            reason = source.content.strip() if source and source.content.strip() else tool.description
            ap = Approval(
                user_id=run.user_id,
                run_id=run.id,
                task_id=task_id,
                bot_id=bot_id,
                tool=tool.name,
                args=args,
                args_hash=h,
                reason=reason,
                level=tool.level,
                expires_at=now() + timedelta(hours=ttl),
            )
            db.add(ap)
            db.flush()
            main = self._main_thread(db, chat)
            add_message(
                db,
                thread=main,
                run_id=run.id,
                sender_type="bot",
                type_="approval",
                content=f"请求执行 {tool.name}",
                sender_bot_id=bot_id,
                meta={
                    "approval_id": ap.id,
                    "reason": reason,
                    "tool_call_id": call.get("id"),
                    "tool": tool.name,
                    "args": args,
                    "level": tool.level,
                    "bot_name": bot.name if bot else "",
                    "status": "pending",
                    "expires_at": ap.expires_at.isoformat() if ap.expires_at else None,
                },
            )
            notify(
                db,
                run.user_id,
                "approval",
                f"需要审批：{tool.name}",
                f"{bot.name if bot else 'Bot'} 请求执行 {tool.name}",
                chat_id=run.chat_id,
                target_type="approval",
                target_id=ap.id,
            )
            emit(
                db,
                run.user_id,
                run.chat_id,
                "approval.created",
                {"approval_id": ap.id, "run_id": run.id, "tool": tool.name, "level": tool.level},
            )
            db.flush()
            aid = ap.id
        raise PauseSignal(
            {
                "kind": "approval",
                "phase": "task" if task_id else "supervisor",
                "task_id": task_id,
                "thread_id": thread_id,
                "bot_id": bot_id,
                "approval_id": aid,
                "tool_call": call,
            }
        )

    async def _execute_builtin(
        self, tool: toolmod.Tool, args: dict, bot_id: str, task_id: str | None,
        *, thread_id: str, call: dict,
    ) -> str:
        if tool.name in ("web_read", "web_search", "code_exec", "run_command"):
            loaded = await asyncio.to_thread(self._load_builtin_context, bot_id, task_id, thread_id, call)
            if isinstance(loaded, str):
                return loaded
            return await self._invoke_builtin(tool, args, loaded)
        return await asyncio.to_thread(
            self._execute_blocking_builtin, tool, args, bot_id, task_id, thread_id, call,
        )

    def _execute_blocking_builtin(self, tool, args, bot_id, task_id, thread_id, call) -> str:
        # 文件/技能/记忆工具只做同步工作；Session 的整个事务留在同一个后台线程。
        with session_scope() as db:
            self._assert_lease(db)
            existing = db.scalar(select(Message).where(Message.thread_id == thread_id, Message.run_id == self.run_id,
                                 Message.type == "tool_result", Message.meta["tool_call_id"].as_string() == call.get("id")))
            if existing:
                return existing.content
            run = db.get(Run, self.run_id)
            chat = db.get(Chat, run.chat_id)
            bot = execution_bot(db, run, bot_id)
            task = db.get(Task, task_id) if task_id else None
            ctx = ToolCtx(db, run, chat, bot, task, self._assert_lease)
            ctx.tool_call_id = call.get("id")
            ctx.thread_id = thread_id
            result = asyncio.run(self._invoke_builtin(tool, args, ctx))
            usage = getattr(ctx, "skill_usage", None)
            if usage and result.startswith(("技能「", "技能选择：")):
                source = call_source(db, run.id, thread_id, bot_id, call.get("id"))
                # 成功读取与结构化证据同事务落库；重放、换工具调用 id 均可复用。
                add_message(db, thread=db.get(Thread, thread_id), run_id=run.id,
                            sender_type="system", type_="tool_result", content=result,
                            meta={"tool_call_id": call.get("id"), "tool": tool.name,
                                  "skill_usage": {**usage, "bot_id": bot_id,
                                      "interjection_seq": (source.meta or {}).get("interjection_seq", 0) if source else 0}})
            return result

    def _load_builtin_context(self, bot_id: str, task_id: str | None, thread_id: str, call: dict):
        with session_scope() as db:
            self._assert_lease(db)
            existing = db.scalar(select(Message).where(
                Message.thread_id == thread_id, Message.run_id == self.run_id,
                Message.type == "tool_result",
                Message.meta["tool_call_id"].as_string() == call.get("id"),
            ))
            if existing:
                return existing.content
            run = db.get(Run, self.run_id)
            chat = db.get(Chat, run.chat_id)
            bot = execution_bot(db, run, bot_id)
            task = db.get(Task, task_id) if task_id else None
            ctx = ToolCtx(db, run, chat, bot, task, self._assert_lease)
            ctx.tool_call_id = call.get("id")
            return ctx

    async def _invoke_builtin(self, tool: toolmod.Tool, args: dict, ctx: ToolCtx) -> str:
        try:
            assert tool.executor is not None
            limit = tool.timeout or 30
            out = await asyncio.wait_for(tool.executor(ctx, **args), timeout=limit)
        except asyncio.TimeoutError:
            return f"工具执行超时（{int(tool.timeout or 30)} 秒），未取得结果。"
        except (ToolError, sandbox.SandboxError) as exc:
            return f"工具执行失败：{exc}"
        except TypeError as exc:
            return f"工具参数错误：{exc}"
        except Exception as exc:  # noqa: BLE001
            return f"工具执行异常：{type(exc).__name__}: {exc}"
        return out or "（无返回）"

    async def _execute_mcp(
        self, tool: toolmod.Tool, args: dict, *, thread_id: str, call: dict
    ) -> str:
        """TOL-05：调用远端 MCP 工具。结果按不可信外部内容标注。"""
        if not tool.server_id or not tool.remote_name:
            return "MCP 工具配置不完整，请到设置页重新检测该服务。"
        loaded = await asyncio.to_thread(self._load_mcp_call, tool.server_id, thread_id, call)
        if isinstance(loaded, str):
            return loaded
        server, token = loaded
        try:
            return await asyncio.wait_for(
                mcpmod.call_remote_tool(server, token, tool.remote_name, args),
                timeout=MCP_TIMEOUT + 5,
            )
        except asyncio.TimeoutError:
            return f"MCP 工具执行超时（{MCP_TIMEOUT + 5} 秒），未取得结果。"
        except mcpmod.McpError as exc:
            return f"MCP 工具执行失败：{exc}"
        except Exception as exc:  # noqa: BLE001
            return f"MCP 工具执行异常：{type(exc).__name__}: {exc}"

    def _load_mcp_call(self, server_id: str, thread_id: str, call: dict):
        with session_scope() as db:
            self._assert_lease(db)
            existing = db.scalar(
                select(Message).where(
                    Message.thread_id == thread_id,
                    Message.run_id == self.run_id,
                    Message.type == "tool_result",
                    Message.meta["tool_call_id"].as_string() == call.get("id"),
                )
            )
            if existing:
                return existing.content
            server = db.get(McpServer, server_id)
            if not server or not server.enabled:
                return "MCP 服务已停用或删除，请到设置页检查。"
            token = decrypt_secret(server.token_encrypted) if server.token_encrypted else None
            return server, token
