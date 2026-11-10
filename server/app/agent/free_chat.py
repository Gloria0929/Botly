"""自由群聊：轮换接话机会，首批并行，后续接着已发表的内容聊。"""
from __future__ import annotations

import asyncio
import hashlib
import re

from sqlalchemy import select

from app.agent.execution_config import execution_provider
from app.agent.llm import LLMError
from app.agent.router import route_members
from app.agent.signals import AwaitChildren
from app.core.config import FREE_CHAT_ROUTER_ENABLED, FREE_CHAT_ROUTER_MODEL, FREE_CHAT_ROUTER_TIMEOUT
from app.core.db import IS_SQLITE, session_scope
from app.core.events import emit
from app.core.logging_setup import get_logger
from app.core.models import Bot, Chat, Message, Run, Task, uid
from app.core.redis_bus import enqueue
from app.core.security import decrypt_secret
from app.core.similarity import normalize

log = get_logger(__name__)

ACTIVE = ("queued", "running", "waiting", "waiting_children")
GROUP_GREETINGS = {"大家好", "你们好", "谢谢大家"}
ROUTER_MAX_PICK = 3


def social_message(content: str) -> bool:
    """识别短问候与收尾；面向大家的招呼仍给其他成员参与机会。"""
    return normalize(content) in {
        "你好", "你们好", "大家好", "嗨", "哈喽", "hello", "hi", "早", "早安", "早上好",
        "下午好", "晚上好", "晚安", "谢谢", "谢谢大家", "多谢", "辛苦了", "好的", "好",
        "嗯", "嗯嗯", "ok", "okay", "收到", "明白了", "知道了", "哈哈", "哈哈哈", "先这样", "再见",
    }


def conversation_body(content: str) -> str:
    body = re.sub(r"```.*?```|~~~.*?~~~", "", content, flags=re.S)
    body = re.sub(r"`[^`\n]*`", "", body)
    return "\n".join(line for line in body.splitlines() if not line.lstrip().startswith(">"))


def mentioned_members(content: str, members: list[Bot]) -> list[str]:
    matches = []
    for bot in members:
        match = re.search(rf"@{re.escape(bot.name)}(?=[\s，。！？、；：,.!?;:]|$)", content)
        if match:
            matches.append((match.start(), bot.id))
    return [bot_id for _, bot_id in sorted(matches)]


def handoff_members(content: str, members: list[Bot]) -> list[str]:
    """识别正文中的接话邀请；普通提及、引用和代码不会唤醒成员。"""
    invited = []
    for line in conversation_body(content).splitlines():
        matches = []
        for bot in members:
            for match in re.finditer(rf"@{re.escape(bot.name)}(?=[\s，。！？、；：,.!?;:]|$)", line):
                tail = line[match.end():].lstrip(" ，,:：")
                if not line[:match.start()].strip() or re.match(
                    r"(?:你(?:们)?|请|帮|来|能否|能不能|要不要|怎么看|觉得|有没有)|[？?]$", tail
                ):
                    matches.append((match.start(), bot.id))
                    break
        invited.extend(bid for _, bid in sorted(matches))
    return list(dict.fromkeys(invited))


def free_run_blocked(db, run: Run) -> bool:
    """在 Worker 的认领锁内检查：同群同成员依用户消息顺序回应。"""
    if run.kind != "free" or not run.trigger_message_id:
        return False
    trigger = db.get(Message, run.trigger_message_id)
    if not trigger:
        return False
    if run.parent_run_id and db.scalar(select(Run.id).where(
        Run.chat_id == run.chat_id, Run.kind == "free", Run.target_bot_id == run.target_bot_id,
        Run.id != run.id, Run.status == "running",
    ).limit(1)):
        return True  # 旧话题后来追加的接话，也不能与已经开口的新回复重叠。
    earlier = db.scalars(select(Run).join(Message, Message.id == Run.trigger_message_id).where(
        Run.chat_id == run.chat_id, Run.kind == "free", Run.status.in_(ACTIVE),
        Message.seq < trigger.seq,
    ).order_by(Message.seq, Run.created_at)).all()
    for other in earlier:
        if not other.parent_run_id:
            # 先把旧消息分发给接话对象，新消息才能知道该排在谁后面。
            if other.status in ("queued", "running") and not other.config_snapshot.get("free", {}).get("direct_started"):
                return True
        elif run.parent_run_id and other.target_bot_id == run.target_bot_id:
            return True
    return False


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
        children = list(db.scalars(select(Run).where(Run.parent_run_id == run.id)))
        if run.kind == "free" and not run.parent_run_id:
            consumed = set(run.config_snapshot.get("free", {}).get("consumed_peers", []))
            if any(child.kind == "free" and child.status not in ACTIVE and child.id not in consumed
                   for child in children):
                return True  # 一位成员返回就继续接话，不等最慢的那位。
        return not any(child.status in ACTIVE for child in children)

    def _participation_order(self, db, run: Run, members: list[Bot]) -> list[str]:
        """积极度影响参与频率，近期已获得机会的成员给其他人让出位置。"""
        opportunities = {bot.id: 0 for bot in members}
        last_chance = {bot.id: 0 for bot in members}
        trigger = db.get(Message, run.trigger_message_id)
        query = select(Run.config_snapshot, Message.seq).join(
            Message, Message.id == Run.trigger_message_id,
        ).where(Run.chat_id == run.chat_id, Run.kind == "free", Run.parent_run_id.is_(None),
                Run.id != run.id)
        if trigger:
            query = query.where(Message.seq < trigger.seq)
        previous = db.execute(query.order_by(Message.seq.desc()).limit(30))
        for config, seq in previous:
            free = config.get("free") or {}
            if free.get("directed"):
                continue
            initial = free.get("initial_bot_ids", [free.get("primary_bot_id")])
            for bid in set(initial):
                if bid in opportunities:
                    opportunities[bid] += 1
                    last_chance[bid] = max(last_chance[bid], seq)
        return [bot.id for bot in sorted(members, key=lambda bot: (
            opportunities[bot.id] / max(0.2, bot.proactivity or 0),
            last_chance[bot.id], -(bot.proactivity or 0),
        ))]

    def _named_partner(self, db, run: Run, members: list[Bot]) -> str | None:
        """用户直接叫名字提问时，对话对象切换到被叫的成员，不必为了换人先输入 @。"""
        trigger = db.get(Message, run.trigger_message_id)
        if not trigger:
            return None
        for bot in members:
            if re.search(rf"(?:^|[，,。！？!?]\s*){re.escape(bot.name)}[，,:：\s]*(?:你|请|帮|怎么看|觉得)",
                         conversation_body(trigger.content)):
                return bot.id
        return None

    def _route_inputs(self) -> dict | None:
        """首次分发前读取路由所需的上下文；已路由、定向、静音、一对一问候或候选不足时返回 None。"""
        with session_scope() as db:
            run = db.get(Run, self.run_id)
            if not run or run.kind != "free" or run.parent_run_id or not run.trigger_message_id:
                return None
            state = (run.config_snapshot or {}).get("free") or {}
            if state.get("direct_started") or state.get("route_done") or state.get("directed"):
                return None
            chat = db.get(Chat, run.chat_id)
            trigger = db.get(Message, run.trigger_message_id)
            if not chat or not trigger or chat.muted:
                return None
            if social_message(trigger.content) and normalize(trigger.content) not in GROUP_GREETINGS:
                return None
            configs = run.config_snapshot.get("bot_configs", {})
            members = [bot for bot in self._member_bots(db, chat, run)
                       if bot.enabled is not False and bot.provider_id and bot.id in configs]
            # 积极度为 0 的成员不参与可选发言，路由也不能把他选出来。
            candidates = [bot for bot in members if bot.proactivity > 0]
            if len(candidates) < 2:
                return None
            leader_id = (run.config_snapshot.get("supervisor") or {}).get("id")
            ordered = sorted(candidates, key=lambda bot: bot.id != leader_id)
            host = provider = None
            for bot in ordered:
                provider = execution_provider(db, run, bot)
                if provider:
                    host = bot
                    break
            if not provider:
                return None
            model = FREE_CHAT_ROUTER_MODEL or host.model or (provider.models[0]["name"] if provider.models else "")
            names = {bot.id: bot.name for bot in members}
            recent = {bot.id: 0 for bot in candidates}
            rows = db.scalars(select(Message).where(
                Message.thread_id == trigger.thread_id, Message.seq < trigger.seq,
                Message.type == "text", Message.status == "done",
            ).order_by(Message.seq.desc()).limit(24))
            history = []
            for message in reversed(list(rows)):
                meta = message.meta or {}
                if meta.get("delegation") or meta.get("subtask_live") or not (message.content or "").strip():
                    continue
                if message.sender_type == "bot":
                    who = names.get(message.sender_bot_id) or "成员"
                    if message.sender_bot_id in recent:
                        recent[message.sender_bot_id] += 1
                else:
                    who = "用户"
                history.append({"who": who, "text": message.content})
            return {
                "kind": provider.kind, "base_url": provider.base_url,
                "api_key": decrypt_secret(provider.api_key_encrypted), "model": model,
                "content": trigger.content, "history": history[-12:],
                "members": [{"id": bot.id, "name": bot.name, "role": bot.role, "persona": bot.persona or "",
                             "proactivity": bot.proactivity, "recent": recent[bot.id]} for bot in candidates],
                "max_pick": max(1, min(int(state.get("max_speakers") or ROUTER_MAX_PICK), ROUTER_MAX_PICK)),
            }

    def _save_route(self, picks: list[dict] | None) -> None:
        with session_scope() as db:
            if IS_SQLITE:
                db.connection().exec_driver_sql("BEGIN IMMEDIATE")
            run = db.scalar(select(Run).where(Run.id == self.run_id).with_for_update())
            self._assert_lease(db, run)
            state = dict(run.config_snapshot["free"])
            if state.get("direct_started"):
                return
            state["route_done"] = True
            state["route"] = list(picks or [])
            self._save_free_state(run, state)

    async def _route_free_message(self) -> None:
        """首次分发前让路由模型按内容挑选接话成员；任何失败都回到原有的轮换规则，不影响回复。"""
        if not FREE_CHAT_ROUTER_ENABLED:
            return
        data = await asyncio.to_thread(self._route_inputs)
        if not data:
            return
        try:
            picks = await route_members(**data, timeout=FREE_CHAT_ROUTER_TIMEOUT)
        except Exception as exc:
            log.warning("自由群聊路由异常，回退到轮换规则：%s", type(exc).__name__)
            picks = None
        await asyncio.to_thread(self._save_route, picks)

    @staticmethod
    def _routed_order(state: dict, eligible: set[str], available: set[str], named: str | None) -> tuple[list[str], dict]:
        """路由建议转成接话顺序。只采纳积极度大于 0 的成员；被用户直接叫到名字的成员排第一。"""
        picks = [item for item in state.get("route") or [] if item.get("bot_id") in eligible]
        order = list(dict.fromkeys(item["bot_id"] for item in picks))
        if order and named in available and order[0] != named:
            order = [named, *[bid for bid in order if bid != named]]
        reasons = {item["bot_id"]: item.get("reason") or "" for item in picks}
        return order[:state["max_speakers"]], reasons

    def _conversation_partner(self, db, run: Run, members: list[Bot]) -> str | None:
        available = {bot.id for bot in members}
        trigger = db.get(Message, run.trigger_message_id)
        if trigger:
            named = self._named_partner(db, run, members)
            if named:
                return named
            # 只有追问才延续对象；普通新话题不能永久绑定同一个成员。
            followup = re.match(
                r"(?:继续|接着|再(?:说|讲|聊|解释|展开|补充)|[你您](?:刚才|说|提|觉得|认为)|刚才|"
                r"为什么(?:这么|这样)|怎么(?:说|做|办)|然后呢|那(?:个|这|你|要|怎么|为什么)|"
                r"还有呢|展开|具体|详细|说下去)", trigger.content.strip(),
            )
            if not followup:
                eligible = [bot for bot in members if bot.proactivity > 0]
                return next(iter(self._participation_order(db, run, eligible or members)), None)
            # 延续用户的对话对象，而不是把最后补充的成员误当成新负责人。
            previous = db.scalars(select(Run).join(Message, Message.id == Run.trigger_message_id).where(
                Run.chat_id == run.chat_id, Run.kind == "free", Run.parent_run_id.is_(None),
                Message.seq < trigger.seq,
            ).order_by(Message.seq.desc()))
            for prior in previous:
                state = prior.config_snapshot.get("free") or {}
                targets = state.get("targets", [])
                partner = targets[0] if state.get("directed") and len(targets) == 1 else state.get("primary_bot_id")
                if partner in available:
                    return partner
            recent = db.scalars(select(Message).where(
                Message.thread_id == trigger.thread_id, Message.seq < trigger.seq,
                Message.sender_type == "bot", Message.type == "text", Message.status == "done",
            ).order_by(Message.seq.desc()).limit(20))
            for message in recent:
                if (message.sender_bot_id in available and not message.meta.get("delegation")
                        and not message.meta.get("free_observer")):
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
                routed, reasons = [], {}
                if not state["directed"]:
                    routed, reasons = self._routed_order(
                        state, {bot.id for bot in members if bot.proactivity > 0}, available,
                        self._named_partner(db, run, members))
                    if routed:
                        state["primary_bot_id"] = routed[0]
                        state["route_reasons"] = reasons
                state.setdefault("speaker_claims", list(dict.fromkeys(state["spoken"])))
                state.setdefault("speaker_turns", [])
                run.round = max(1, run.round)
                targets = state["targets"] if state["directed"] else [bot.id for bot in members
                    if bot.id == state["primary_bot_id"] or bot.proactivity > 0]
                if not state["directed"]:
                    trigger = db.get(Message, run.trigger_message_id)
                    if trigger and social_message(trigger.content) and normalize(trigger.content) not in GROUP_GREETINGS:
                        targets = [state["primary_bot_id"]] if state["primary_bot_id"] else []
                    if routed:
                        # 路由按内容挑出的人就是本条消息的参与者；没被选中的成员不再凑数。
                        targets = list(routed)
                    else:
                        order = self._participation_order(db, run, members)
                        targets.sort(key=lambda bid: (bid != state["primary_bot_id"], order.index(bid)))
                state["initial_bot_ids"] = targets[:min(2, state["max_speakers"])]
                queue = [{"bot_id": bid, "required": state["directed"] or bid == state["primary_bot_id"],
                          "primary": bid == state["primary_bot_id"], "round": run.round,
                          "initial": bid in state["initial_bot_ids"],
                          **({"route_reason": reasons[bid]} if reasons.get(bid) else {})}
                         for bid in targets if bid not in state["spoken"]]
            children = list(db.scalars(select(Run).where(Run.parent_run_id == run.id).order_by(Run.created_at, Run.id)))
            peers = [child for child in children if child.kind == "free"]
            consumed = set(state["consumed_peers"])
            for peer in peers:
                if peer.id in consumed or peer.status in ACTIVE:
                    continue
                consumed.add(peer.id)
                result = peer.config_snapshot.get("free") or {}
                if result.get("spoken"):
                    state["spoken"] = list(dict.fromkeys([*state["spoken"], peer.target_bot_id]))
                    source_id = result.get("response_message_id")
                    source = db.get(Message, source_id) if source_id else None
                    latest = db.get(Message, state["latest_message_id"]) if state.get("latest_message_id") else None
                    if source and (not latest or source.seq > latest.seq):
                        state["latest_message_id"] = source_id
                    if not state["everyone"]:
                        for bid in result.get("mentions", []):
                            queue.append({"bot_id": bid, "required": True, "round": peer.round + 1,
                                          "handoff_message_id": source_id})
                        if (source and not result.get("mentions") and peer.round < state["max_rounds"]
                                and re.search(r"[？?][”’\"']*\s*$", conversation_body(source.content))):
                            # 群友问了一个开放问题，先前说过话的人也能接回来；仍由它自己决定是否发言。
                            for bot in members:
                                if (bot.id != peer.target_bot_id and bot.proactivity > 0
                                        and (bot.id in state["spoken"] or not any(item["bot_id"] == bot.id for item in queue))):
                                    queue.append({"bot_id": bot.id, "required": False, "followup": True,
                                                  "round": peer.round + 1, "reply_to_message_id": source_id})
            state["consumed_peers"] = list(consumed)
            active_peers = [peer for peer in peers if peer.status in ACTIVE]
            scheduled = {(entry["bot_id"], entry["round"]) for entry in state["scheduled"]}
            trigger = db.get(Message, run.trigger_message_id)
            newer_message = bool(trigger and db.scalar(select(Message.id).where(
                Message.thread_id == trigger.thread_id, Message.sender_type == "user", Message.type == "text",
                Message.seq > trigger.seq,
            ).limit(1)))
            queue = [item for item in queue if item["bot_id"] in available]
            # 主接话者失败/被停用时，由下一位可用成员接话，不生成虚假的「收到」。
            if not active_peers and not state["directed"] and not state["spoken"] and not chat.muted:
                if not any(item["required"] and (item["bot_id"], item["round"]) not in scheduled for item in queue):
                    fallback = next((bot.id for bot in members if not any(bid == bot.id for bid, _ in scheduled)), None)
                    if not fallback:
                        tried = {peer.target_bot_id for peer in peers
                                 if peer.config_snapshot.get("free", {}).get("selected", {}).get("required")}
                        fallback = next((bot.id for bot in members if bot.id not in tried and any(
                            peer.target_bot_id == bot.id and peer.status == "completed" for peer in peers)), None)
                    if fallback:
                        state["primary_bot_id"] = fallback
                        queue.insert(0, {"bot_id": fallback, "required": True, "primary": True,
                                         "round": 1, "fallback": True})
            queue.sort(key=lambda item: (not item["required"], not item.get("followup", False)))
            ids = []
            pending_queue = []
            in_flight = {peer.target_bot_id for peer in active_peers if peer.kind == "free"}
            for candidate in queue:
                bid = candidate["bot_id"]
                key = bid, candidate["round"]
                if (bid not in available or candidate["round"] > state["max_rounds"]
                        or (key in scheduled and not candidate.get("fallback"))):
                    continue
                if chat.muted and not state["directed"] and not candidate["required"]:
                    continue
                if not candidate["required"] and (newer_message or (not candidate.get("followup")
                        and any(entry["bot_id"] == bid for entry in state["scheduled"]))):
                    continue
                if state["everyone"] and any(entry["bot_id"] == bid for entry in state["scheduled"]):
                    continue
                claims = state["speaker_claims"]
                if bid not in claims and len(claims) >= state["max_speakers"]:
                    continue
                if len(state["speaker_turns"]) >= state["max_speakers"] * state["max_rounds"]:
                    continue
                # 普通消息最多同时生成两位成员的回复；一位完成即可补入下一位。
                # 已在生成的成员预留人数位置，避免快模型抢完全部名额。
                reserved = set(claims) | in_flight
                if (bid in in_flight or (not state["directed"] and (
                        len(in_flight) >= 2 or (bid not in reserved and len(reserved) >= state["max_speakers"])))):
                    pending_queue.append(candidate)
                    continue
                if (not state["directed"] and not candidate.get("initial") and in_flight
                        and not consumed):
                    pending_queue.append(candidate)
                    continue
                state["turn"] += 1
                if not candidate["required"] and not candidate.get("reply_to_message_id"):
                    candidate["reply_to_message_id"] = state.get("latest_message_id")
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
                in_flight.add(bid)
                state["peer_ids"] = [*state["peer_ids"], child.id]
                state["scheduled"] = [*state["scheduled"], {"bot_id": bid, "round": candidate["round"]}]
                scheduled.add(key)
                run.round = max(run.round, candidate["round"])
            state["queue"] = pending_queue
            waiting = bool(ids or active_peers or any(child.kind == "chime" and child.status in ACTIVE for child in children))
            state["phase"] = "responding" if waiting else "idle"
            self._save_free_state(run, state)
            error = "成员模型调用失败，未能回应本条消息" if not waiting and not state["spoken"] and any(
                peer.status == "failed" for peer in peers) else None
            return ids, waiting, error

    async def choose_free_peers(self) -> dict:
        await asyncio.to_thread(self._check_limits)
        await self._route_free_message()
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
                if not selected["required"] and not run.config_snapshot["free"].get("has_started"):
                    trigger = db.get(Message, run.trigger_message_id)
                    if trigger and db.scalar(select(Message.id).where(
                        Message.thread_id == trigger.thread_id, Message.sender_type == "user", Message.type == "text",
                        Message.seq > trigger.seq,
                    ).limit(1)):
                        can_reply = False
                if can_reply and current and current.enabled is not False and not current.deleted_at and any(bot.id == current.id for bot in members):
                    return {"role": "peer", "bot_id": selected["bot_id"], "thread_id": run.thread_id,
                            "task_id": None, "last": "", "has_calls": False, "cont": False, "done": False}
        if root:
            return await self.choose_free_peers()
        await self.finish_run("")
        return {"done": True, "cont": False}

    def _claim_free_speaker(self, content: str | None = None) -> bool:
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
            if not selected["required"] and not run.config_snapshot["free"].get("has_started"):
                trigger = db.get(Message, run.trigger_message_id)
                if trigger and db.scalar(select(Message.id).where(
                    Message.thread_id == trigger.thread_id, Message.sender_type == "user", Message.type == "text",
                    Message.seq > trigger.seq,
                ).limit(1)):
                    return False
            claims = list(state.get("speaker_claims", []))
            turns = list(state.get("speaker_turns", []))
            bid = selected["bot_id"]
            key = f"{bid}:{selected['round']}:{selected['turn']}"
            # 完整回复在同一把配额锁内去重，避免并行成员都在对方提交前通过检查。
            replies = dict(state.get("reply_claims", {}))
            if content and not state["directed"] and (not selected["required"] or selected.get("primary")):
                digest = hashlib.sha256((normalize(content) or content.strip()).encode()).hexdigest()
                if any(value == digest for turn, value in replies.items() if turn != key):
                    state["reply_claims"] = {turn: value for turn, value in replies.items() if turn != key}
                    state["speaker_turns"] = [turn for turn in turns if turn != key]
                    if not any(turn.startswith(f"{bid}:") for turn in state["speaker_turns"]):
                        state["speaker_claims"] = [speaker for speaker in claims if speaker != bid]
                    self._save_free_state(root, state)
                    return False
                replies[key] = digest
                state["reply_claims"] = replies
            if key in turns:
                self._save_free_state(root, state)
                return True
            addressed = state["directed"] or (selected["required"] and not selected.get("primary"))
            if chat.muted and not addressed:
                return False
            if bid not in claims and len(claims) >= state["max_speakers"]:
                return False
            if len(turns) >= state["max_speakers"] * state["max_rounds"]:
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
                response = db.scalar(select(Message).where(
                    Message.run_id == run.id, Message.sender_bot_id == bot_id, Message.type == "text",
                    Message.status == "done",
                ).order_by(Message.seq.desc()))
                state["response_message_id"] = response.id if response else None
                delegated = set(db.scalars(select(Task.assigned_bot_id).where(Task.run_id == run.id)))
                state["mentions"] = [] if state["everyone"] else [
                    bid for bid in handoff_members(content, members) if bid != bot_id and bid not in delegated]
            self._save_free_state(run, state)
            emit(db, run.user_id, run.chat_id, "bot.typing",
                 {"bot_id": bot_id, "run_id": run.parent_run_id or run.id, "typing": False})
            return bool(run.parent_run_id)

    def _duplicate_free_reply(self, content: str) -> bool:
        normalized = normalize(content) or content.strip()
        with session_scope() as db:
            run = db.get(Run, self.run_id)
            trigger = db.get(Message, run.trigger_message_id)
            voting = bool(trigger and re.search(r"同意|赞同|支持|投票|表态|举手", trigger.content))
            if not voting and re.fullmatch(
                r"(?:我也?|也|完全|基本)?(?:同意|赞同|认同|支持)(?:你们?|大家|以上|前面|上面|这个)?"
                r"的?(?:说法|观点|建议|看法|方案|结论)?|没错|确实如此|说得对|有道理|没有(?:其他|更多)?补充",
                normalized,
            ):
                return True
            peers = select(Run.id).where(Run.parent_run_id == run.parent_run_id, Run.id != run.id)
            return any((normalize(message.content) or message.content.strip()) == normalized
                       for message in db.scalars(select(Message).where(
                           Message.run_id.in_(peers), Message.type == "text", Message.status == "done",
                           Message.sender_type == "bot",
                       )) if message.content.strip())

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
