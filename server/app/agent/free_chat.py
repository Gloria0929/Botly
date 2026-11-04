"""自由群聊直接回应；静默与发言上限在实际输出时处理。"""
from __future__ import annotations

import asyncio
import re

from sqlalchemy import select

from app.agent.llm import LLMError
from app.agent.signals import AwaitChildren
from app.core.db import IS_SQLITE, session_scope
from app.core.events import emit
from app.core.models import Bot, Chat, Message, Run, now, uid
from app.core.redis_bus import enqueue

ACTIVE = ("queued", "running", "waiting", "waiting_children")


def mentioned_members(content: str, members: list[Bot]) -> list[str]:
    matches = []
    for bot in members:
        match = re.search(rf"@{re.escape(bot.name)}(?=[\s，。！？、；：,.!?;:]|$)", content)
        if match:
            matches.append((match.start(), bot.id))
    return [bot_id for _, bot_id in sorted(matches)]


class FreeChatMixin:
    async def execute_chime(self) -> None:
        """兼容旧队列：退休的判断执行直接结束，不再调用模型。"""
        await asyncio.to_thread(self._close_run, "completed", "已移除独立发言判断")
        await asyncio.to_thread(self._wake_retired_parent)

    def _wake_retired_parent(self) -> None:
        with session_scope() as db:
            run = db.get(Run, self.run_id)
            parent_id = run.parent_run_id if run else None
        if parent_id:
            self._wake_parent(parent_id)

    def _save_free_state(self, run: Run, state: dict) -> None:
        run.config_snapshot = {**run.config_snapshot, "free": state}

    def _free_ready(self, db, run: Run) -> bool:
        state = (run.config_snapshot or {}).get("free") or {}
        children = list(db.scalars(select(Run).where(Run.parent_run_id == run.id)))
        if not any(child.status in ACTIVE for child in children):
            return True
        consumed = set(state.get("consumed_peers", []))
        return any(child.kind == "free" and child.id not in consumed and child.status not in ACTIVE
                   for child in children)

    def _conversation_partner(self, db, run: Run, members: list[Bot]) -> str | None:
        available = {bot.id for bot in members}
        trigger = db.get(Message, run.trigger_message_id)
        if trigger:
            recent = db.scalars(select(Message).where(
                Message.thread_id == trigger.thread_id, Message.seq < trigger.seq,
                Message.sender_type == "bot", Message.type == "text", Message.status == "done",
            ).order_by(Message.seq.desc()).limit(20))
            for message in recent:
                if message.sender_bot_id in available and not message.meta.get("delegation"):
                    return message.sender_bot_id
        leader = (run.config_snapshot.get("supervisor") or {}).get("id")
        return leader if leader in available else members[0].id if members else None

    def _parallel_free_batch(self) -> tuple[list[str], bool, str | None]:
        with session_scope() as db:
            if IS_SQLITE:
                db.connection().exec_driver_sql("BEGIN IMMEDIATE")
            run = db.scalar(select(Run).where(Run.id == self.run_id).with_for_update())
            self._assert_lease(db, run)
            chat = db.get(Chat, run.chat_id)
            state = dict(run.config_snapshot["free"])
            members = [bot for bot in self._member_bots(db, chat, run) if bot.enabled is not False
                       and bot.provider_id and bot.id in run.config_snapshot.get("bot_configs", {})]
            available = {bot.id for bot in members}
            queue = list(state.get("queue", []))
            if not state.get("direct_started"):
                state["direct_started"] = True
                state.setdefault("consumed_peers", [])
                state.setdefault("scheduled", [])
                state.setdefault("peer_ids", [])
                state["primary_bot_id"] = None if state["directed"] else self._conversation_partner(db, run, members)
                state.setdefault("speaker_claims", list(dict.fromkeys(state["spoken"])))
                state.setdefault("speaker_turns", [])
                run.round = max(1, run.round)
                targets = state["targets"] if state["directed"] else [bot.id for bot in members]
                if not state["directed"]:
                    targets.sort(key=lambda bid: bid != state["primary_bot_id"])
                queue = [{"bot_id": bid, "required": state["directed"] or bid == state["primary_bot_id"],
                          "primary": bid == state["primary_bot_id"], "round": run.round}
                         for bid in targets if bid not in state["spoken"]]
            children = list(db.scalars(select(Run).where(Run.parent_run_id == run.id)))
            peers = [child for child in children if child.kind == "free"]
            consumed = set(state["consumed_peers"])
            for peer in peers:
                if peer.id in consumed or peer.status in ACTIVE:
                    continue
                consumed.add(peer.id)
                result = peer.config_snapshot.get("free") or {}
                if result.get("spoken"):
                    state["spoken"] = [*state["spoken"], peer.target_bot_id]
                    if not state["everyone"]:
                        for bid in result.get("mentions", []):
                            queue.append({"bot_id": bid, "required": True, "round": peer.round + 1})
            state["consumed_peers"] = list(consumed)
            active_peers = [peer for peer in peers if peer.status in ACTIVE]
            scheduled = {(entry["bot_id"], entry["round"]) for entry in state["scheduled"]}
            ids = []
            for candidate in queue:
                bid = candidate["bot_id"]
                key = bid, candidate["round"]
                if bid not in available or candidate["round"] > state["max_rounds"] or key in scheduled:
                    continue
                if chat.muted and not state["directed"] and not candidate["required"]:
                    continue
                if state["everyone"] and any(entry["bot_id"] == bid for entry in state["scheduled"]):
                    continue
                claims = state["speaker_claims"]
                if bid not in claims and len(claims) >= state["max_speakers"]:
                    continue
                if len(state["speaker_turns"]) >= state["max_speakers"] * state["max_rounds"]:
                    continue
                state["turn"] += 1
                candidate.update(turn=state["turn"], root_run_id=run.id)
                child_id = uid()
                child = Run(id=child_id, user_id=run.user_id, chat_id=run.chat_id, kind="free",
                            round=candidate["round"], parent_run_id=run.id, target_bot_id=bid,
                            trigger_message_id=run.trigger_message_id, thread_id=run.thread_id, mode=run.mode,
                            config_snapshot={**run.config_snapshot, "task_workspace": f"tasks/{child_id}",
                                "free": {"parallel_child": True, "direct_response": True, "selected": candidate,
                                         "primary_bot_id": state["primary_bot_id"], "spoken": [], "queue": [],
                                         "turn": candidate["turn"], "everyone": state["everyone"],
                                         "directed": True, "targets": [bid], "max_speakers": state["max_speakers"],
                                         "max_rounds": candidate["round"]}})
                db.add(child)
                db.flush()
                ids.append(child.id)
                state["peer_ids"] = [*state["peer_ids"], child.id]
                state["scheduled"] = [*state["scheduled"], {"bot_id": bid, "round": candidate["round"]}]
                scheduled.add(key)
                run.round = max(run.round, candidate["round"])
            state["queue"] = []
            waiting = bool(ids or active_peers or any(child.kind == "chime" and child.status in ACTIVE for child in children))
            state["phase"] = "responding" if waiting else "idle"
            self._save_free_state(run, state)
            error = "成员模型调用失败，未能回应本条消息" if not waiting and not state["spoken"] and any(
                peer.status == "failed" for peer in peers) else None
            return ids, waiting, error

    async def choose_free_peers(self) -> dict:
        await asyncio.to_thread(self._check_limits)
        ids, waiting, error = await asyncio.to_thread(self._parallel_free_batch)
        for child_id in ids:
            await asyncio.to_thread(enqueue, child_id)
        if waiting:
            raise AwaitChildren()
        if error:
            raise LLMError(error)
        await self.finish_run("")
        return {"done": True, "cont": False}

    async def choose_peer(self) -> dict:
        """成员直接进入回应；旧顶层检查点也切到直接分发。"""
        await asyncio.to_thread(self._check_limits)
        with session_scope() as db:
            run = db.get(Run, self.run_id)
            root = run.parent_run_id is None
            if not root:
                selected = run.config_snapshot["free"]["selected"]
                current = db.get(Bot, selected["bot_id"])
                chat = db.get(Chat, run.chat_id)
                members = self._member_bots(db, chat, run)
                parent = db.get(Run, run.parent_run_id)
                addressed = bool(parent and parent.config_snapshot.get("free", {}).get("directed")) or (
                    selected["required"] and not selected.get("primary"))
                can_reply = not chat.muted or addressed or run.config_snapshot["free"].get("has_started")
                if can_reply and current and current.enabled is not False and not current.deleted_at and any(bot.id == current.id for bot in members):
                    return {"role": "peer", "bot_id": selected["bot_id"], "thread_id": run.thread_id,
                            "task_id": None, "last": "", "has_calls": False, "cont": False, "done": False}
        if root:
            return await self.choose_free_peers()
        await self.finish_run("")
        return {"done": True, "cont": False}

    def _claim_free_speaker(self) -> bool:
        """并行成员实际输出时才占用配额，不能预先按名单凑人数。"""
        with session_scope() as db:
            if IS_SQLITE:
                db.connection().exec_driver_sql("BEGIN IMMEDIATE")
            run = db.get(Run, self.run_id)
            self._assert_lease(db, run)
            selected = run.config_snapshot["free"]["selected"]
            root_id = run.parent_run_id or run.id
            root = db.scalar(select(Run).where(Run.id == root_id).with_for_update()
                             .execution_options(populate_existing=True))
            if root.status not in ACTIVE or root.stop_requested:
                return False
            state = dict(root.config_snapshot["free"])
            chat = db.get(Chat, root.chat_id)
            claims = list(state.get("speaker_claims", []))
            turns = list(state.get("speaker_turns", []))
            bid = selected["bot_id"]
            key = f"{bid}:{selected['round']}:{selected['turn']}"
            if key in turns:
                return True
            addressed = state["directed"] or (selected["required"] and not selected.get("primary"))
            if chat.muted and not addressed:
                return False
            primary = state.get("primary_bot_id")
            reserve_primary = bool(primary and primary not in claims and bid != primary)
            if bid not in claims and len(claims) >= state["max_speakers"] - int(reserve_primary):
                return False
            if len(turns) >= state["max_speakers"] * state["max_rounds"] - int(reserve_primary):
                return False
            state["speaker_claims"] = claims if bid in claims else [*claims, bid]
            state["speaker_turns"] = [*turns, key]
            self._save_free_state(root, state)
            free = dict(run.config_snapshot["free"])
            free["has_started"] = True
            self._save_free_state(run, free)
            emit(db, run.user_id, run.chat_id, "bot.typing",
                 {"bot_id": bid, "run_id": root_id, "typing": True})
            return True

    async def finish_peer(self, bot_id: str, content: str) -> dict:
        independent = await asyncio.to_thread(self._finish_peer, bot_id, content)
        if independent:
            await self.finish_run(content)
            return {"done": True, "cont": False}
        return {"role": "peer", "bot_id": "", "task_id": None, "cont": True, "has_calls": False}

    def _finish_peer(self, bot_id: str, content: str) -> bool:
        with session_scope() as db:
            run = db.get(Run, self.run_id)
            self._assert_lease(db, run)
            state = dict(run.config_snapshot["free"])
            selected = state["selected"]
            if state.get("processed_turn", 0) >= selected["turn"]:
                return bool(run.parent_run_id)
            state["processed_turn"] = selected["turn"]
            if content.strip() and content.strip() != "[PASS]":
                state["spoken"] = [*state["spoken"], bot_id]
                members = self._member_bots(db, db.get(Chat, run.chat_id), run)
                state["mentions"] = [] if state["everyone"] else [
                    bid for bid in mentioned_members(content, members) if bid != bot_id]
            self._save_free_state(run, state)
            emit(db, run.user_id, run.chat_id, "bot.typing",
                 {"bot_id": bot_id, "run_id": run.parent_run_id or run.id, "typing": False})
            return bool(run.parent_run_id)

    def _mark_peer_pass(self, message_id: str) -> None:
        with session_scope() as db:
            run = db.get(Run, self.run_id)
            self._assert_lease(db, run)
            message = db.get(Message, message_id)
            if message:
                db.delete(message)
                db.flush()
                emit(db, run.user_id, run.chat_id, "message.deleted", {"id": message_id})
            state = dict(run.config_snapshot["free"])
            state["pass_turn"] = state["selected"]["turn"]
            self._save_free_state(run, state)
            run.steps_used += 1
