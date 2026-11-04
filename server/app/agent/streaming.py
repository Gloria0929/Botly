"""流式消息的草稿、增量事件与完成态持久化。"""
from __future__ import annotations

import asyncio
import time
from sqlalchemy import func, select

from app.agent.messages import add_message
from app.core.db import session_scope
from app.core.events import emit
from app.core.models import Event, Message, Run, Thread


class StreamingMixin:
    def _open_stream_message(self, thread_id: str, bot_id: str, meta: dict | None = None) -> dict:
        """先建草稿，第一段文字到达时再广播，纯工具轮不会闪现空气泡。"""
        with session_scope() as db:
            self._assert_lease(db)
            thread = db.get(Thread, thread_id)
            msg = add_message(
                db, thread=thread, run_id=self.run_id, sender_type="bot", type_="text",
                content="", sender_bot_id=bot_id, status="streaming", meta=meta or {},
                emit_event=False,
            )
            return {
                "id": msg.id, "announced": False, "thread_id": msg.thread_id,
                "user_id": msg.user_id, "chat_id": thread.chat_id,
                "last_content": "", "last_persisted_at": 0.0,
                "last_event_id": int(db.scalar(select(func.max(Event.id))) or 0),
                "created": {
                    "id": msg.id, "thread_id": msg.thread_id, "run_id": msg.run_id,
                    "seq": msg.seq, "sender_type": msg.sender_type, "sender_bot_id": msg.sender_bot_id,
                    "type": msg.type, "meta": msg.meta or {}, "status": "streaming",
                    "created_at": msg.created_at.isoformat(),
                },
            }

    async def _flush_delta(self, states: list[dict], buffer: str, force: bool) -> None:
        """同一轮文本同时刷新任务线程和群聊镜像，节流只判断一次。"""
        if not buffer.strip() or all(s.get("last_content") == buffer for s in states):
            return
        stamp = time.monotonic()
        if not force and all(s["announced"] for s in states) and stamp - self._last_flush < 0.2:
            return
        self._last_flush = stamp
        await asyncio.gather(*(asyncio.to_thread(self._write_delta, state, buffer) for state in states))

    def _write_delta(self, state: dict, buffer: str) -> None:
        if state.get("last_content") == buffer:
            return
        stamp = time.monotonic()
        persist = stamp - state["last_persisted_at"] >= 1.5
        announced = state["announced"]
        with session_scope() as db:
            self._assert_lease(db)
            msg = db.get(Message, state["id"]) if persist else None
            if persist:
                if not msg:
                    return
            if not announced:
                if not buffer.strip():
                    return
                ev = emit(db, state["user_id"], state["chat_id"], "message.created",
                          {**state["created"], "content": buffer})
            else:
                delta = buffer[len(state["last_content"]):]
                if not delta:
                    return
                ev = emit(db, state["user_id"], state["chat_id"], "message.delta", {
                    "message_id": state["id"], "thread_id": state["thread_id"], "delta": delta,
                    "offset": len(state["last_content"]),
                })
            if persist:
                msg.content = buffer
                # 以前一个已提交事件作为回放起点；当前事件在提交阶段才获得 ID。
                msg.meta = {**(msg.meta or {}), "stream_event_id": state["last_event_id"]}
        state["last_content"] = buffer
        state["announced"] = True
        state["last_event_id"] = ev.id
        if persist:
            state["last_persisted_at"] = stamp

    def _drop_message(self, message_id: str) -> None:
        with session_scope() as db:
            self._assert_lease(db)
            msg = db.get(Message, message_id)
            if not msg:
                return
            chat_id = db.get(Thread, msg.thread_id).chat_id
            user_id = msg.user_id
            db.delete(msg)
            db.flush()
            emit(db, user_id, chat_id, "message.deleted", {"id": message_id})

    def _finalize_message(
        self, state: dict, content: str, status: str = "done", reasoning: str = "",
        meta: dict | None = None, mark_turn: bool = False, type_: str | None = None,
    ) -> None:
        with session_scope() as db:
            self._assert_lease(db)
            msg = db.get(Message, state["id"])
            if not msg:
                return
            msg.content = content
            msg.reasoning_content = reasoning
            msg.status = status
            if type_:
                msg.type = type_
            if meta:
                msg.meta = {**(msg.meta or {}), **meta}
            db.flush()
            if mark_turn and msg.run_id:
                run = db.get(Run, msg.run_id)
                if run:
                    run.last_turn_message_id = msg.id
                    run.steps_used += 1
            chat_id = db.get(Thread, msg.thread_id).chat_id
            if not state["announced"]:
                state["announced"] = True
                emit(db, msg.user_id, chat_id, "message.created", {
                    "id": msg.id,
                    "thread_id": msg.thread_id,
                    "run_id": msg.run_id,
                    "seq": msg.seq,
                    "sender_type": msg.sender_type,
                    "sender_bot_id": msg.sender_bot_id,
                    "type": msg.type,
                    "content": content,
                    "meta": msg.meta or {},
                    "status": status,
                    "created_at": msg.created_at.isoformat(),
                })
                return
            emit(db, msg.user_id, chat_id, "message.completed", {
                "message_id": msg.id, "thread_id": msg.thread_id, "content": content,
                "status": status, "type": msg.type, "meta": msg.meta or {},
            })
