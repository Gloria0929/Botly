"""LangGraph 编排：总指挥与子 Bot 共用一张 ReAct 状态图。

图结构（每个 run 一条 thread，checkpoint 落到 SQLite/Postgres）：

    START → model ──有工具调用──→ tool ──还有未执行的调用──→ tool
              ↑                     │
              └─────────────────────┘（一轮调用执行完）
              │
              └──没有工具调用──→ after ──子 Bot 交回结果──→ model（切回总指挥）
                                   │
                                   └──总指挥结束──→ END

设计要点：
- 暂停点用 `interrupt()`：计划确认、提问、审批、逐步确认。恢复时节点会重新执行，
  因此节点里的副作用必须幂等（审批按 args_hash 去重、计划/提问按 tool_call_id 去重）。
- 「派活」不是递归调用，而是切换图里的角色状态：tool 节点建子线程与任务后把
  role 从 supervisor 切成 worker，图继续跑子 Bot 的 ReAct；子 Bot 结束后 after
  节点写回 delegate 的工具结果，再把角色切回总指挥。
- 每次工具调用是图里的一步，所以一轮多个调用可以逐个暂停、逐个恢复，不会重放
  已经执行过的调用。
"""
from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any, TYPE_CHECKING, TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt
from .config import DATABASE_URL, DATA_DIR
from .llm import LLMError
from .signals import PauseSignal

if TYPE_CHECKING:  # pragma: no cover
    from langgraph.checkpoint.base import BaseCheckpointSaver

    from .orchestrator import RunExecutor


class AgentState(TypedDict, total=False):
    role: str  # supervisor | worker
    bot_id: str
    thread_id: str
    task_id: str | None
    last: str  # 最近一轮模型输出，收尾时用
    has_calls: bool
    cont: bool  # after 之后是否继续（子 Bot 交回总指挥）
    done: bool


# --------------------------------------------------------------------------- #
# 检查点：与业务库分开，SQLite 用独立文件，Postgres 用同一个库
# --------------------------------------------------------------------------- #
def checkpoint_path() -> str:
    return str(DATA_DIR / "langgraph_checkpoints.db")


@asynccontextmanager
async def open_checkpointer():
    if DATABASE_URL.startswith("postgresql"):
        from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

        # psycopg 不认 SQLAlchemy 的 "+psycopg" 方言后缀
        conn = DATABASE_URL.replace("postgresql+psycopg://", "postgresql://", 1)
        async with AsyncPostgresSaver.from_conn_string(conn) as saver:
            await saver.setup()
            yield saver
    else:
        from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

        async with AsyncSqliteSaver.from_conn_string(checkpoint_path()) as saver:
            await saver.setup()
            yield saver


# --------------------------------------------------------------------------- #
# 节点
# --------------------------------------------------------------------------- #
def build_graph(ex: "RunExecutor", checkpointer: "BaseCheckpointSaver[Any]"):
    async def model_node(state: AgentState) -> dict:
        role = state.get("role", "supervisor")
        try:
            has_calls, content = await ex.model_turn(
                role=role,
                bot_id=state["bot_id"],
                thread_id=state["thread_id"],
                task_id=state.get("task_id"),
            )
        except LLMError as exc:
            # 子 Bot 失败不拖垮整个执行：记失败原因，把控制权交回总指挥
            if role == "worker" and state.get("task_id"):
                patch = await ex.fail_task(task_id=state["task_id"], reason=str(exc))
                return {**patch, "has_calls": False}
            raise
        return {"has_calls": has_calls, "last": content, "cont": False}

    async def tool_node(state: AgentState) -> dict:
        msg = ex.next_open_call(state["thread_id"])
        if msg is None:
            return {}
        ex.begin_tool(msg.id)
        call = ((msg.meta or {}).get("tool_calls") or [{}])[0]
        name = (call.get("function") or {}).get("name") or ""
        role = state.get("role", "supervisor")

        if name == "delegate":
            if role != "supervisor":
                ex.write_tool_result(state["thread_id"], call, "只有总指挥可以派活。")
                return {}
            return await ex.start_delegate(
                thread_id=state["thread_id"],
                bot_id=state["bot_id"],
                task_id=state.get("task_id"),
                call=call,
            )

        args = dict(
            thread_id=state["thread_id"],
            bot_id=state["bot_id"],
            task_id=state.get("task_id"),
            call=call,
            role=role,
        )
        try:
            outcome = await ex.execute_tool(**args)
        except PauseSignal as sig:
            # 暂停：interrupt 抛出 GraphInterrupt；恢复后返回 resume 值。
            # 此时审批/回答已落库，重放同一个调用就会拿到最终结果。
            interrupt(sig.payload)
            outcome = await ex.execute_tool(**args)
        ex.write_tool_result(state["thread_id"], call, outcome)
        return {}

    async def after_node(state: AgentState) -> dict:
        if state.get("role") == "worker" and state.get("task_id"):
            return await ex.finish_task(task_id=state["task_id"], content=state.get("last") or "")
        ex.finish_run(state.get("last") or "")
        return {"done": True, "cont": False}

    def route_model(state: AgentState) -> str:
        if state.get("cont"):
            # 子 Bot 失败已切回总指挥，由总指挥处理失败结果后再结束。
            return "tool" if ex.next_open_call(state["thread_id"]) else "model"
        return "tool" if state.get("has_calls") else "after"

    def route_tool(state: AgentState) -> str:
        return "tool" if ex.next_open_call(state["thread_id"]) else "model"

    def route_after(state: AgentState) -> str:
        if state.get("cont"):
            return "tool" if ex.next_open_call(state["thread_id"]) else "model"
        return END

    g = StateGraph(AgentState)
    g.add_node("model", model_node)
    g.add_node("tool", tool_node)
    g.add_node("after", after_node)
    g.add_edge(START, "model")
    g.add_conditional_edges("model", route_model, {"tool": "tool", "after": "after", "model": "model"})
    g.add_conditional_edges("tool", route_tool, {"tool": "tool", "model": "model"})
    g.add_conditional_edges("after", route_after, {"tool": "tool", "model": "model", END: END})
    return g.compile(checkpointer=checkpointer)


# --------------------------------------------------------------------------- #
# 执行入口
# --------------------------------------------------------------------------- #
def run_config(run_id: str) -> dict:
    # 不按业务步数或图节点跳转次数停止；token、时长和用户停止仍由执行器检查。
    return {"configurable": {"thread_id": f"run:{run_id}"}, "recursion_limit": float("inf")}


async def run_graph(
        ex: "RunExecutor",
        state: AgentState,
        resume: dict | None = None,
):
    """跑一次图。返回 interrupt payload（暂停）或 None（结束）。"""
    cfg = run_config(ex.run_id)
    async with open_checkpointer() as saver:
        app = build_graph(ex, saver)
        snap = await app.aget_state(cfg)
        started = bool(snap.values)

        if resume is not None and started:
            out = await app.ainvoke(Command(resume=resume), cfg)
        elif started and snap.next:
            # 僵尸执行回收：从最近一次检查点继续
            out = await app.ainvoke(None, cfg)
        elif started:
            ex.finish_run(snap.values.get("last") or "")
            return None  # 这个 run 已经跑完，避免重复执行
        else:
            if resume is not None:
                # 有暂停记录但没有检查点（旧数据/检查点被清理）：补好工具结果后重跑
                ex.answer_open_calls("上一次工具调用因执行中断没有返回结果，请根据当前情况重新判断。")
            out = await app.ainvoke(state, cfg)

    pending = (out or {}).get("__interrupt__")
    if pending:
        return pending[0].value
    return None
