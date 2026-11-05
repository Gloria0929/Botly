"""群聊、消息与执行触发。"""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from pathlib import PurePosixPath
from urllib.parse import quote
from uuid import uuid4
from typing import Literal

from fastapi import APIRouter, BackgroundTasks, Depends, File, HTTPException, Query, Response, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy import delete, func, or_, select
from sqlalchemy.orm import Session

from app.sandbox import files as sandbox
from app.sandbox.artifacts import visible_files
from app.agent.capabilities import supervisor_block_reason
from app.core.db import IS_SQLITE, get_db
from app.core.config import DELEGATION_DEPTH, SANDBOX_FILE_MAX_MB
from app.core.deps import current_user, owned_or_404
from app.core.events import emit
from app.agent.execution_config import capture_bots
from app.agent.free_chat import mentioned_members
from app.agent.graph import open_checkpointer
from app.core.models import (
    Approval,
    Bot,
    Chat,
    ChatMember,
    Event,
    FileRec,
    Message,
    Notification,
    Provider,
    Question,
    Run,
    Task,
    Thread,
    TerminalCommand,
    UsageLog,
    User,
    UserSettings,
    now,
)
from app.agent.orchestrator import RunExecutor, add_message, emit_run_status, emit_task_status
from app.core.redis_bus import enqueue
from app.agent.serializers import bot_out, chat_out, iso, message_out, run_out, task_out
from app.services.chat_templates import CHAT_TEMPLATES, TEMPLATE_BY_ID, assemble_members, template_out

router = APIRouter(prefix="/chats", tags=["chats"])

# RUN-12：waiting_children 也属于进行中，群聊仍然「忙」，不允许再起新执行
ACTIVE = ("queued", "running", "waiting", "waiting_children")


class ChatIn(BaseModel):
    name: str
    member_ids: list[str] = []
    supervisor_bot_id: str | None = None
    mode: Literal["task", "free"] = "task"
    max_speakers: int = Field(default=3, ge=1, le=6)
    max_rounds: int = Field(default=2, ge=1, le=2)
    muted: bool = False


class ChatTemplateIn(BaseModel):
    template_id: str
    mode: Literal["task", "free"] = "task"


class ChatPatch(BaseModel):
    name: str | None = None
    supervisor_bot_id: str | None = None
    member_ids: list[str] | None = None
    run_mode: str | None = None
    limits_override: dict | None = None
    always_allow_tools: list[str] | None = None
    require_approval_for_write: bool | None = None
    mode: Literal["task", "free"] | None = None
    max_speakers: int | None = Field(default=None, ge=1, le=6)
    max_rounds: int | None = Field(default=None, ge=1, le=2)
    muted: bool | None = None


class MessageIn(BaseModel):
    content: str
    mention_bot_id: str | None = None
    mention_bot_ids: list[str] = Field(default_factory=list, max_length=6)
    everyone: bool = False
    reply_to: str | None = None
    # 执行中补充（RUN-13）：为 True 时不新建执行，只把消息交给正在跑的执行
    interject: bool = False
    # CHT-07：分支线程里的消息，为空表示主线程
    thread_id: str | None = None


class BranchIn(BaseModel):
    message_id: str
    title: str | None = None


class RetryTaskIn(BaseModel):
    note: str = Field(default="", max_length=2000)


class MembersIn(BaseModel):
    bot_ids: list[str] = []


def _members(db: Session, chat_id: str) -> list[Bot]:
    return list(
        db.scalars(
            select(Bot)
            .join(ChatMember, ChatMember.bot_id == Bot.id)
            .where(ChatMember.chat_id == chat_id)
            .order_by(Bot.created_at)
        ).all()
    )


def _active_run(db: Session, chat_id: str) -> Run | None:
    # 只看顶层执行：并行子执行也是 Run，但它不应该成为「当前群聊在执行什么」
    # 的答案，否则引导会写进子任务线程、停止也停错对象（RUN-12）。
    return db.scalar(
        select(Run)
        .where(
            Run.chat_id == chat_id,
            Run.status.in_(ACTIVE),
            Run.parent_run_id.is_(None),
        )
        .order_by(Run.created_at.desc())
    )


def _run_children_totals(db: Session, run: Run | None) -> tuple[int, int]:
    """RUN-12：并行子执行的 token/步数，展示时合并到父执行。"""
    if not run or run.parent_run_id:
        return 0, 0
    row = db.execute(
        select(
            func.coalesce(func.sum(Run.tokens_used), 0),
            func.coalesce(func.sum(Run.steps_used), 0),
        ).where(Run.parent_run_id == run.id)
    ).one()
    return int(row[0] or 0), int(row[1] or 0)


def _main_thread(db: Session, chat: Chat) -> Thread:
    thread = db.scalar(
        select(Thread).where(Thread.chat_id == chat.id, Thread.type == "main")
    )
    if not thread:
        thread = Thread(user_id=chat.user_id, chat_id=chat.id, type="main", title="主线程")
        db.add(thread)
        db.flush()
    return thread


def _system_note(db: Session, chat: Chat, text: str, meta: dict | None = None) -> None:
    """成员变动写一条系统消息，群聊里留痕，也让后续执行能看到。"""
    add_message(
        db,
        thread=_main_thread(db, chat),
        run_id=None,
        sender_type="system",
        type_="system",
        content=text,
        meta=meta or {},
    )


def _members_event(db: Session, chat: Chat, members: list[Bot]) -> None:
    emit(
        db,
        chat.user_id,
        chat.id,
        "chat.members",
        {
            "chat_id": chat.id,
            "supervisor_bot_id": chat.supervisor_bot_id,
            "members": [
                {"id": m.id, "name": m.name, "avatar": m.avatar, "role": m.role}
                for m in members
            ],
        },
    )


def _owned_bots(db: Session, user_id: str, bot_ids: list[str]) -> dict[str, Bot]:
    """批量校验 Bot 归属；不存在、已删除、别人的一律按无效处理。"""
    clean = list(dict.fromkeys(i for i in bot_ids if i))
    if not clean:
        return {}
    rows = db.scalars(
        select(Bot).where(Bot.user_id == user_id, Bot.id.in_(clean))
    ).all()
    return {b.id: b for b in rows if b.deleted_at is None}


@router.get("")
def list_chats(user: User = Depends(current_user), db: Session = Depends(get_db)):
    chats = db.scalars(
        select(Chat)
        .where(Chat.user_id == user.id, Chat.archived.is_(False))
        .order_by(Chat.last_active_at.desc())
    ).all()
    if not chats:
        return []
    chat_ids = [c.id for c in chats]
    active_rows = db.scalars(select(Run).where(
        Run.user_id == user.id, Run.chat_id.in_(chat_ids), Run.status.in_(ACTIVE),
        Run.parent_run_id.is_(None),
    ).order_by(Run.created_at.desc())).all()
    active_by_chat = {}
    for run in active_rows:
        active_by_chat.setdefault(run.chat_id, run.status)
    latest = select(
        Thread.chat_id.label("chat_id"), Message.content.label("content"),
        func.row_number().over(partition_by=Thread.chat_id,
            order_by=(Message.created_at.desc(), Message.seq.desc(), Message.id.desc())).label("rank"),
    ).join(Thread, Thread.id == Message.thread_id).where(
        Message.user_id == user.id, Thread.chat_id.in_(chat_ids), Message.type == "text",
    ).subquery()
    previews = dict(db.execute(select(latest.c.chat_id, latest.c.content).where(latest.c.rank == 1)).all())
    member_counts = dict(db.execute(select(ChatMember.chat_id, func.count(ChatMember.bot_id)).where(
        ChatMember.user_id == user.id, ChatMember.chat_id.in_(chat_ids),
    ).group_by(ChatMember.chat_id)).all())
    return [
            {
                "id": c.id,
                "name": c.name,
                "run_mode": c.run_mode,
                "mode": c.mode,
                "last_active_at": c.last_active_at.isoformat() if c.last_active_at else None,
                "run_status": active_by_chat.get(c.id),
                "preview": (previews.get(c.id) or "")[:60],
                "member_count": member_counts.get(c.id, 0),
            }
        for c in chats
    ]


@router.post("", status_code=201)
def create_chat(
    payload: ChatIn,
    background: BackgroundTasks,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    bots = db.scalars(
        select(Bot).where(Bot.user_id == user.id, Bot.deleted_at.is_(None))
    ).all()
    if not bots:
        raise HTTPException(400, "请先创建至少一个 Bot")
    by_id = {b.id: b for b in bots}
    member_ids = payload.member_ids or [b.id for b in bots]
    member_ids = list(dict.fromkeys(i for i in member_ids if i in by_id))
    if not member_ids:
        raise HTTPException(400, "至少选择一个成员 Bot")

    providers = {
        p.id: p
        for p in db.scalars(select(Provider).where(Provider.user_id == user.id)).all()
    }

    def _blocked(bid: str) -> str | None:
        bot = by_id[bid]
        if bot.enabled is False:
            return f"Bot「{bot.name}」已停用，不能作为总指挥"
        return supervisor_block_reason(bot, providers.get(bot.provider_id or ""))

    sup_id = payload.supervisor_bot_id
    if payload.mode == "free":
        if sup_id and (sup_id not in by_id or by_id[sup_id].enabled is False):
            raise HTTPException(400, "负责人必须是可用的成员")
    elif sup_id and sup_id in by_id:
        # MOD-02：模型明确不支持工具调用 / 流式输出时，不允许当总指挥
        reason = _blocked(sup_id)
        if reason:
            raise HTTPException(400, reason)
    else:
        # 自动挑选：优先总指挥模板，跳过明确不能用的模型；都不合格时退回第一个成员
        candidates = [b.id for b in bots if b.is_supervisor_template] + member_ids
        sup_id = next((bid for bid in candidates if not _blocked(bid)), member_ids[0])
    if sup_id and sup_id not in member_ids:
        member_ids.insert(0, sup_id)
    if payload.mode == "free" and not 2 <= len(member_ids) <= 6:
        raise HTTPException(400, "自由群聊需要 2–6 位成员")

    chat = Chat(user_id=user.id, name=payload.name.strip() or "新群聊", supervisor_bot_id=sup_id,
                mode=payload.mode, max_speakers=payload.max_speakers,
                max_rounds=payload.max_rounds, muted=payload.muted)
    db.add(chat)
    db.flush()
    for bid in member_ids:
        db.add(ChatMember(chat_id=chat.id, bot_id=bid, user_id=user.id))
    db.add(Thread(user_id=user.id, chat_id=chat.id, type="main", title="主线程"))
    db.flush()
    # 仅预热已配置的沙箱后端；未接入时建群不启动任何容器。
    from app.sandbox import runtime as linux_sandbox

    background.add_task(linux_sandbox.warm, user.id, chat.id)
    return chat_out(chat, by_id.get(sup_id), _members(db, chat.id))


@router.get("/templates")
def list_chat_templates(user: User = Depends(current_user)):
    return [template_out(template) for template in CHAT_TEMPLATES]


@router.post("/from-template", status_code=201)
def create_chat_from_template(
    payload: ChatTemplateIn,
    background: BackgroundTasks,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    template = TEMPLATE_BY_ID.get(payload.template_id)
    if not template:
        raise HTTPException(404, "群聊模板不存在")
    members, created_ids, setup_required = assemble_members(db, user.id, template)
    chat = create_chat(ChatIn(
        name=template["name"], member_ids=[b.id for b in members], supervisor_bot_id=members[0].id,
        mode=payload.mode,
    ), background, user, db)
    return {**chat, "created_bot_ids": created_ids, "model_setup_required": setup_required}


@router.get("/{chat_id}")
def get_chat(chat_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    chat = owned_or_404(db, Chat, chat_id, user.id)
    members = _members(db, chat.id)
    sup = db.get(Bot, chat.supervisor_bot_id) if chat.supervisor_bot_id else None
    active = _active_run(db, chat.id)
    active_roots = list(db.scalars(select(Run).where(Run.chat_id == chat.id, Run.parent_run_id.is_(None),
                                                 Run.status.in_(ACTIVE)).order_by(Run.created_at.desc()))) if chat.mode == "free" else []
    tasks = []
    status_map: dict[str, str] = {}
    # 没有进行中的执行时，回退到最近一次有任务的执行，保证任务面板不会在完成后清空
    panel_run = active
    if not panel_run:
        panel_run = db.scalar(
            select(Run)
            .where(
                Run.chat_id == chat.id,
                Run.parent_run_id.is_(None),
                select(Task.id).where(Task.run_id == Run.id).exists(),
            )
            .order_by(Run.created_at.desc())
        )
    if panel_run:
        # RUN-09：并行子执行里再派出的孙任务挂在子执行下，任务面板要一并列出
        panel_root_ids = [r.id for r in active_roots] if active_roots else [panel_run.id]
        panel_run_ids = panel_root_ids + list(
            db.scalars(select(Run.id).where(Run.parent_run_id.in_(panel_root_ids)))
        )
        rows = db.scalars(
            select(Task).where(Task.run_id.in_(panel_run_ids)).order_by(Task.created_at)
        ).all()
        for t in rows:
            tasks.append(task_out(t, db.get(Bot, t.assigned_bot_id)))
            if active and t.status == "running":
                status_map[t.assigned_bot_id] = "working"
            elif active and t.status in ("waiting_approval", "waiting_reply"):
                status_map[t.assigned_bot_id] = "waiting"
    if active:
        supervisor = ((active.config_snapshot or {}).get("supervisor") or {}).get("id") or chat.supervisor_bot_id
        if active.kind == "free":
            free = (active.config_snapshot or {}).get("free", {})
            selected = free.get("selected") or {}
            supervisor = selected.get("bot_id") if selected.get("turn", 0) > free.get("processed_turn", 0) else None
        if active.status == "running" and "working" not in status_map.values():
            status_map[active.target_bot_id or supervisor] = "working"
        elif active.status == "waiting":
            bot_id = (active.resume_payload or {}).get("bot_id") or active.target_bot_id or supervisor
            if "waiting" not in status_map.values():
                status_map[bot_id] = "waiting"
    data = chat_out(chat, sup, members)
    main_thread = db.scalar(
        select(Thread).where(Thread.chat_id == chat.id, Thread.type == "main")
    )
    data["main_thread_id"] = main_thread.id if main_thread else None
    typing_runs: dict[str, list[str]] = {}
    if active_roots:
        peer_runs = db.scalars(select(Run).where(Run.parent_run_id.in_([r.id for r in active_roots]),
                                               Run.kind == "free", Run.status.in_(ACTIVE))).all()
        for peer in peer_runs:
            free = peer.config_snapshot.get("free") or {}
            if peer.status == "queued":
                continue  # 排队接话不显示成正在工作，也不能覆盖另一轮的等待审批状态。
            if free.get("direct_response") and not free.get("has_started") and not free.get("selected", {}).get("required"):
                continue
            status_map[peer.target_bot_id] = "waiting" if peer.status == "waiting" else "working"
            if peer.status != "waiting":
                typing_runs.setdefault(peer.target_bot_id, []).append(peer.parent_run_id)
    data["members"] = [
        {**bot_out(m), "status": status_map.get(m.id, "idle"), "typing_run_ids": list(set(typing_runs.get(m.id, [])))} for m in members
    ]
    data["active_run"] = run_out(active, *_run_children_totals(db, active)) if active else None
    data["active_runs"] = [run_out(r, *_run_children_totals(db, r)) for r in active_roots]
    latest_run = active or db.scalar(
        select(Run)
        .where(Run.chat_id == chat.id, Run.parent_run_id.is_(None))
        .order_by(Run.created_at.desc())
    )
    data["last_run"] = (
        run_out(latest_run, *_run_children_totals(db, latest_run)) if latest_run else None
    )
    data["tasks"] = tasks
    pending = db.scalars(select(Approval).join(Run, Run.id == Approval.run_id).where(
        Run.chat_id == chat.id, Run.status.in_(ACTIVE), Approval.status == "pending",
    ).order_by(Approval.created_at)).all()
    data["pending_approvals"] = [{"id": a.id, "tool": a.tool,
        "bot_name": db.get(Bot, a.bot_id).name if db.get(Bot, a.bot_id) else "Bot"} for a in pending]
    data["files"] = [
        {"id": f.id, "path": f.path, "size": f.size, "updated_at": f.updated_at.isoformat(),
         "run_id": f.run_id, "created_by_bot_id": f.created_by_bot_id}
        for f in visible_files(db.scalars(
            select(FileRec).where(FileRec.chat_id == chat.id).order_by(FileRec.path)
        ).all())
    ]
    settings = db.get(UserSettings, user.id)
    chat_limits = chat.limits_override or {}
    data["limits"] = {
        "max_steps": 0,
        "token_budget": chat_limits.get("token_budget")
                        if chat_limits.get("token_budget") is not None
                        else (settings.token_budget if settings else 300000),
        "delegation_depth": chat_limits.get("delegation_depth")
                            if chat_limits.get("delegation_depth") is not None
                            else ((settings.delegation_depth or DELEGATION_DEPTH) if settings else DELEGATION_DEPTH),
    }
    return data


@router.patch("/{chat_id}")
def update_chat(
        chat_id: str,
        payload: ChatPatch,
        user: User = Depends(current_user),
        db: Session = Depends(get_db),
):
    chat = owned_or_404(db, Chat, chat_id, user.id)
    data = payload.model_dump(exclude_unset=True)
    if any(data.get(key) is None for key in ("mode", "max_speakers", "max_rounds", "muted") if key in data):
        raise HTTPException(400, "群聊模式与发言设置不能为空")
    next_mode = data.get("mode", chat.mode)
    if "mode" in data and next_mode != chat.mode and _active_run(db, chat.id):
        raise HTTPException(409, "请等当前执行完成或停止后再切换群聊模式")
    proposed_members = data.get("member_ids")
    if next_mode == "free" and not 2 <= len(set(proposed_members if proposed_members is not None
                                                else [m.id for m in _members(db, chat.id)])) <= 6:
        raise HTTPException(400, "自由群聊需要 2–6 位成员")
    if "limits_override" in data:
        override = data["limits_override"]
        if not isinstance(override, dict):
            raise HTTPException(400, "限额覆盖必须是对象")
        unknown = set(override) - {"token_budget", "delegation_depth"}
        if unknown:
            raise HTTPException(400, f"不支持的限额项：{'、'.join(sorted(unknown))}")
        budget = override.get("token_budget")
        if budget is not None and (type(budget) is not int or (budget != 0 and budget < 1000)):
            raise HTTPException(400, "token 预算应为 0（无限制）或至少 1000")
        depth = override.get("delegation_depth")
        if depth is not None and (type(depth) is not int or not 1 <= depth <= 5):
            raise HTTPException(400, "委派深度应为 1–5 层")
    if "run_mode" in data and data["run_mode"] not in ("auto", "step"):
        raise HTTPException(400, "状态模式只能是 auto 或 step")

    old_supervisor = chat.supervisor_bot_id
    new_supervisor = data.get("supervisor_bot_id", old_supervisor)
    if next_mode == "task" and not new_supervisor:
        raise HTTPException(400, "任务模式必须指定总指挥")
    if "supervisor_bot_id" in data:
        if not new_supervisor and next_mode == "free":
            data.pop("supervisor_bot_id")
            chat.supervisor_bot_id = None
    if "supervisor_bot_id" in data or (data.get("mode") == "task" and chat.mode == "free"):
        sup_bot = owned_or_404(db, Bot, new_supervisor, user.id)
        if sup_bot.deleted_at:
            raise HTTPException(400, "该 Bot 已被删除，不能担任总指挥")
        if sup_bot.enabled is False:
            raise HTTPException(400, f"Bot「{sup_bot.name}」已停用，不能担任总指挥")
        # MOD-02：同上，模型明确不支持工具调用 / 流式输出时不接受为新总指挥
        provider = db.get(Provider, sup_bot.provider_id) if sup_bot.provider_id else None
        reason = supervisor_block_reason(sup_bot, provider) if next_mode == "task" else None
        if reason:
            raise HTTPException(400, reason)

    member_ids = data.pop("member_ids", None)
    members_before = {m.id: m for m in _members(db, chat.id)}
    if member_ids is not None:
        if not member_ids:
            raise HTTPException(400, "群聊至少保留一个成员")
        clean = list(dict.fromkeys(i for i in member_ids if i))
        found = _owned_bots(db, user.id, clean)
        unknown = [i for i in clean if i not in found]
        if unknown:
            raise HTTPException(400, "Bot 不存在或已被删除")
        if new_supervisor and new_supervisor not in found:
            raise HTTPException(400, "总指挥必须在群聊成员中，请先更换总指挥")

        # 与 DELETE /members/{id} 保持一致：执行中的成员不能被 PATCH 悄悄移出
        removing = [bid for bid in members_before if bid not in found]
        active = _active_run(db, chat.id)
        if active and removing:
            busy = set(db.scalars(
                select(Task.assigned_bot_id).where(
                    Task.run_id == active.id,
                    Task.assigned_bot_id.in_(removing),
                    Task.status.in_(("queued", "running", "waiting_approval", "waiting_reply")),
                )
            ).all())
            if busy:
                names = "、".join(members_before[bid].name for bid in busy if bid in members_before)
                raise HTTPException(409, f"{names} 有子任务正在执行，请等完成或停止执行后再移出")

        for m in db.scalars(select(ChatMember).where(ChatMember.chat_id == chat.id)).all():
            if m.bot_id not in found:
                db.delete(m)
        db.flush()
        for bid in clean:
            if bid not in members_before:
                db.add(ChatMember(chat_id=chat.id, bot_id=bid, user_id=user.id))
        db.flush()

        added = [found[i].name for i in clean if i not in members_before]
        removed = [b.name for i, b in members_before.items() if i not in found]
        if added:
            _system_note(
                db,
                chat,
                "已加入成员：" + "、".join(added),
                {"kind": "members_added", "names": added},
            )
        if removed:
            _system_note(
                db,
                chat,
                "已移除成员：" + "、".join(removed),
                {"kind": "members_removed", "names": removed},
            )
    else:
        # 只改总指挥时，也要保证新总指挥本来就是成员
        if new_supervisor and new_supervisor != old_supervisor:
            if new_supervisor not in members_before:
                raise HTTPException(400, "请先把该 Bot 加入群聊，再设为总指挥")

    if new_supervisor and new_supervisor != old_supervisor:
        _system_note(
            db,
            chat,
            f"已将总指挥更换为 {db.get(Bot, new_supervisor).name}",
            {"kind": "supervisor_changed", "bot_id": new_supervisor},
        )

    for k, v in data.items():
        setattr(chat, k, v)
    db.flush()

    if new_supervisor != old_supervisor or member_ids is not None:
        _members_event(db, chat, _members(db, chat.id))
        chat.last_active_at = now()
    sup = db.get(Bot, chat.supervisor_bot_id) if chat.supervisor_bot_id else None
    return chat_out(chat, sup, _members(db, chat.id))


@router.post("/{chat_id}/members")
def add_members(
        chat_id: str,
        payload: MembersIn,
        user: User = Depends(current_user),
        db: Session = Depends(get_db),
):
    """加入成员：已在群里的忽略，可重复调用。"""
    chat = owned_or_404(db, Chat, chat_id, user.id)
    clean = list(dict.fromkeys(i for i in payload.bot_ids if i))
    if not clean:
        raise HTTPException(400, "请选择要加入的 Bot")
    found = _owned_bots(db, user.id, clean)
    if len(found) != len(clean):
        raise HTTPException(400, "Bot 不存在或已被删除")

    current = {m.id for m in _members(db, chat.id)}
    added = [b for bid, b in found.items() if bid not in current]
    if chat.mode == "free" and len(current) + len(added) > 6:
        raise HTTPException(400, "自由群聊最多 6 位成员")
    if added:
        for b in added:
            db.add(ChatMember(chat_id=chat.id, bot_id=b.id, user_id=user.id))
        db.flush()
        _system_note(
            db,
            chat,
            "已加入成员：" + "、".join(b.name for b in added),
            {"kind": "members_added", "bot_ids": [b.id for b in added]},
        )
        chat.last_active_at = now()
        _members_event(db, chat, _members(db, chat.id))
        db.flush()

    sup = db.get(Bot, chat.supervisor_bot_id) if chat.supervisor_bot_id else None
    return chat_out(chat, sup, _members(db, chat.id))


@router.delete("/{chat_id}/members/{bot_id}")
def remove_member(
        chat_id: str,
        bot_id: str,
        user: User = Depends(current_user),
        db: Session = Depends(get_db),
):
    """移出成员：总指挥不能直接移出，执行中的成员也要等任务结束。"""
    chat = owned_or_404(db, Chat, chat_id, user.id)
    bot = owned_or_404(db, Bot, bot_id, user.id)
    link = db.scalar(
        select(ChatMember).where(
            ChatMember.chat_id == chat.id, ChatMember.bot_id == bot.id
        )
    )
    if not link:
        raise HTTPException(404, "该 Bot 不在群聊里")
    if chat.supervisor_bot_id == bot.id:
        raise HTTPException(409, "该 Bot 是群聊总指挥，请先把总指挥换成其他成员")
    members = _members(db, chat.id)
    if chat.mode == "free" and len(members) <= 2:
        raise HTTPException(409, "自由群聊至少保留 2 位成员")
    if len(members) <= 1:
        raise HTTPException(409, "群聊至少保留一个成员")

    active = _active_run(db, chat.id)
    if active:
        busy = db.scalar(
            select(func.count(Task.id)).where(
                Task.run_id == active.id,
                Task.assigned_bot_id == bot.id,
                Task.status.in_(("queued", "running", "waiting_approval", "waiting_reply")),
            )
        )
        if busy:
            raise HTTPException(409, "该 Bot 有子任务正在执行，请等它完成或停止执行后再移出")

    db.delete(link)
    db.flush()
    _system_note(
        db,
        chat,
        f"已移除成员：{bot.name}",
        {"kind": "members_removed", "bot_ids": [bot.id]},
    )
    chat.last_active_at = now()
    _members_event(db, chat, _members(db, chat.id))
    db.flush()

    sup = db.get(Bot, chat.supervisor_bot_id) if chat.supervisor_bot_id else None
    return chat_out(chat, sup, _members(db, chat.id))


@router.delete("/{chat_id}")
async def delete_chat(chat_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    """解散群聊：按外键依赖顺序清理，最后删群聊本身（需求 CHT-06、8.5）。"""
    chat = owned_or_404(db, Chat, chat_id, user.id)
    if _active_run(db, chat.id):
        raise HTTPException(409, "该群聊有执行正在进行，请先停止")

    # 在业务删除事务前清理远端实例，避免 SQLite 写锁与后台清理相互等待。
    db.commit()
    from app.sandbox import runtime as linux_sandbox
    try:
        await linux_sandbox.release(user.id, chat.id)
    except Exception:
        raise HTTPException(400, "Cube 沙箱释放失败，群聊已保留，请检查沙箱连接后重试")

    thread_ids = list(db.scalars(select(Thread.id).where(Thread.chat_id == chat.id)).all())
    run_ids = list(db.scalars(select(Run.id).where(Run.chat_id == chat.id)).all())
    if run_ids:
        async with open_checkpointer() as saver:
            for run_id in run_ids:
                await saver.adelete_thread(f"run:{run_id}")

    if thread_ids:
        db.execute(delete(Message).where(Message.thread_id.in_(thread_ids)))
        db.execute(delete(Task).where(Task.thread_id.in_(thread_ids)))
        db.flush()
    if run_ids:
        db.execute(delete(Approval).where(Approval.run_id.in_(run_ids)))
        db.execute(delete(Question).where(Question.run_id.in_(run_ids)))
        db.execute(delete(UsageLog).where(UsageLog.run_id.in_(run_ids)))
        db.execute(delete(Run).where(Run.id.in_(run_ids)))
        db.flush()

    db.execute(delete(Thread).where(Thread.chat_id == chat.id))
    db.execute(delete(ChatMember).where(ChatMember.chat_id == chat.id))
    db.execute(delete(FileRec).where(FileRec.chat_id == chat.id))
    db.execute(delete(Event).where(Event.chat_id == chat.id))
    # 通知里的 chat_id/target 会指向已删除的群聊，一并清掉
    db.execute(delete(Notification).where(Notification.chat_id == chat.id))
    db.execute(delete(TerminalCommand).where(TerminalCommand.chat_id == chat.id, TerminalCommand.user_id == user.id))
    db.flush()

    db.delete(chat)
    db.flush()

    # 沙箱文件在磁盘上，随群聊一起清掉
    try:
        sandbox.delete_chat_dir(user.id, chat.id)
    except sandbox.SandboxError:
        pass
    return {"ok": True}


@router.get("/{chat_id}/branches")
def list_branches(
    chat_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)
):
    """CHT-07：列出从主线程拉出的分支。"""
    chat = owned_or_404(db, Chat, chat_id, user.id)
    rows = db.scalars(
        select(Thread)
        .where(Thread.chat_id == chat.id, Thread.type == "branch")
        .order_by(Thread.created_at.desc())
    ).all()
    out = []
    for t in rows:
        count = db.scalar(
            select(func.count(Message.id)).where(Message.thread_id == t.id)
        ) or 0
        out.append(
            {
                "id": t.id,
                "title": t.title or "分支",
                "anchor_message_id": t.anchor_message_id,
                "message_count": int(count),
                "created_at": iso(t.created_at),
            }
        )
    return out


@router.post("/{chat_id}/branches", status_code=201)
def create_branch(
    chat_id: str,
    payload: BranchIn,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    """CHT-07：从任意一条主线程消息拉出分支线程，不打断主线程。

    分支创建时把「锚点及之前的纯文本消息」复制一份作为快照，之后分支独立演进；
    只复制文本，不带工具调用，避免出现 assistant(tool_calls) 缺 tool 结果的非法历史。
    """
    chat = owned_or_404(db, Chat, chat_id, user.id)
    main = db.scalar(
        select(Thread).where(Thread.chat_id == chat.id, Thread.type == "main")
    )
    if not main:
        raise HTTPException(404, "群聊没有主线程")
    anchor = owned_or_404(db, Message, payload.message_id, user.id)
    if anchor.thread_id != main.id:
        raise HTTPException(400, "只能从主线程的消息拉分支")
    title = (payload.title or "").strip()[:120]
    if not title:
        title = " ".join((anchor.content or "分支").split())[:40] or "分支"
    thread = Thread(
        user_id=user.id,
        chat_id=chat.id,
        parent_thread_id=main.id,
        type="branch",
        title=title,
        anchor_message_id=anchor.id,
    )
    db.add(thread)
    db.flush()
    src = db.scalars(
        select(Message)
        .where(
            Message.thread_id == main.id,
            Message.seq <= anchor.seq,
            Message.type == "text",
            Message.status != "streaming",
        )
        .order_by(Message.seq)
    ).all()
    thread.seq_counter = len(src)
    for i, m in enumerate(src, 1):
        db.add(
            Message(
                user_id=user.id,
                thread_id=thread.id,
                run_id=None,
                seq=i,
                sender_type=m.sender_type,
                sender_bot_id=m.sender_bot_id,
                type="text",
                content=m.content,
                meta={"branch_copy": True, "source_message_id": m.id},
                status="done",
            )
        )
    db.flush()
    return {
        "id": thread.id,
        "title": title,
        "anchor_message_id": anchor.id,
        "parent_thread_id": main.id,
        "message_count": len(src),
        "created_at": iso(thread.created_at),
    }


@router.delete("/{chat_id}/branches/{thread_id}")
def delete_branch(
    chat_id: str,
    thread_id: str,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    """CHT-07：删除分支线程，连同分支里的消息、执行与审批提问。

    主线程完全不受影响；分支正在执行时先请求停止并返回 409，
    避免执行器把消息写进已删除的线程（外键冲突）。
    """
    chat = owned_or_404(db, Chat, chat_id, user.id)
    thread = owned_or_404(db, Thread, thread_id, user.id)
    if thread.chat_id != chat.id or thread.type != "branch":
        raise HTTPException(404, "分支不存在")

    run_ids = list(
        db.scalars(select(Run.id).where(Run.user_id == user.id, Run.thread_id == thread.id)).all()
    )
    if run_ids:
        active = list(
            db.scalars(
                select(Run).where(Run.id.in_(run_ids), Run.status.in_(ACTIVE))
            ).all()
        )
        if active:
            for run in active:
                run.stop_requested = True
            # 异常会让 get_db 回滚，所以先提交停止标记，再返回 409
            db.commit()
            raise HTTPException(409, "分支正在执行，已请求停止；请稍后再删除")

    counts: dict[str, int] = {}
    if run_ids:
        counts["approvals"] = int(
            db.execute(delete(Approval).where(Approval.run_id.in_(run_ids))).rowcount or 0
        )
        counts["questions"] = int(
            db.execute(delete(Question).where(Question.run_id.in_(run_ids))).rowcount or 0
        )
    task_cond = Task.thread_id == thread.id
    if run_ids:
        task_cond = or_(task_cond, Task.run_id.in_(run_ids))
    counts["tasks"] = int(
        db.execute(delete(Task).where(Task.user_id == user.id, task_cond)).rowcount or 0
    )
    counts["messages"] = int(
        db.execute(delete(Message).where(Message.thread_id == thread.id)).rowcount or 0
    )
    if run_ids:
        counts["runs"] = int(db.execute(delete(Run).where(Run.id.in_(run_ids))).rowcount or 0)
    db.delete(thread)
    db.flush()
    return {"ok": True, **counts}


@router.get("/{chat_id}/messages")
def list_messages(
        chat_id: str,
        before: int | None = Query(default=None),
        limit: int = Query(default=50, ge=1, le=200),
        thread_id: str | None = Query(default=None),
        around: str | None = Query(default=None),
        user: User = Depends(current_user),
        db: Session = Depends(get_db),
):
    chat = owned_or_404(db, Chat, chat_id, user.id)
    # 先取游标再读消息；两次读取之间产生的事件可重复接收，但不会漏掉。
    event_cursor = db.scalar(
        select(func.max(Event.id)).where(Event.chat_id == chat.id, Event.user_id == user.id)
    ) or 0
    if thread_id:
        thread = owned_or_404(db, Thread, thread_id, user.id)
        if thread.chat_id != chat.id:
            raise HTTPException(404, "线程不存在")
        q = select(Message).where(Message.thread_id == thread_id)
    else:
        main = db.scalar(select(Thread).where(Thread.chat_id == chat.id, Thread.type == "main"))
        if not main:
            return {"messages": [], "has_more": False, "event_cursor": event_cursor}
        q = select(Message).where(Message.thread_id == main.id)
    if around:
        anchor = owned_or_404(db, Message, around, user.id)
        expected_thread = thread_id if thread_id else main.id
        if anchor.thread_id != expected_thread:
            raise HTTPException(404, "消息不属于当前线程")
        q = q.where(Message.seq <= anchor.seq + limit // 2)
    elif before:
        q = q.where(Message.seq < before)
    rows = list(db.scalars(q.order_by(Message.seq.desc()).limit(limit + 1)).all())
    has_more = len(rows) > limit
    rows = rows[:limit]
    rows.reverse()
    # 草稿内容每 1.5 秒落库，快照补齐游标之前的增量，避免重连跳过尚未落库的文字。
    streaming = {m.id: m for m in rows if m.status == "streaming"}
    live_content = {mid: m.content or "" for mid, m in streaming.items()}
    if streaming:
        persisted_id = min(int((m.meta or {}).get("stream_event_id", 0)) for m in streaming.values())
        deltas = db.scalars(select(Event).where(
            Event.user_id == user.id, Event.chat_id == chat.id,
            Event.id > persisted_id, Event.id <= event_cursor, Event.type == "message.delta",
            Event.payload["message_id"].as_string().in_(streaming),
        ).order_by(Event.id)).all()
        for ev in deltas:
            payload = ev.payload or {}
            mid = payload["message_id"]
            delta, offset = payload.get("delta"), payload.get("offset")
            content = live_content[mid]
            if isinstance(delta, str) and isinstance(offset, int) and 0 <= offset <= len(content) < offset + len(delta):
                live_content[mid] = content + delta[len(content) - offset:]
    # 已删除或已移出的 Bot 不在成员列表里，历史消息补上发送者信息
    sender_ids = {m.sender_bot_id for m in rows if m.sender_bot_id}
    sender_bots: dict[str, Bot] = {}
    if sender_ids:
        sender_bots = {
            b.id: b
            for b in db.scalars(select(Bot).where(Bot.id.in_(sender_ids))).all()
        }
    task_ids = {(m.meta or {}).get("task_id") for m in rows} - {None}
    task_map = {t.id: t for t in db.scalars(select(Task).where(Task.id.in_(task_ids))).all()} if task_ids else {}
    serialized = []
    run_ids = {m.run_id for m in rows if m.run_id}
    run_map = {r.id: r for r in db.scalars(select(Run).where(Run.id.in_(run_ids))).all()} if run_ids else {}
    # 主 Agent 没有 Task；按执行与发送者汇总自己的模型用量，历史消息也能补齐。
    agent_usage = {
        (run_id, bot_id): int(tokens or 0)
        for run_id, bot_id, tokens in db.execute(
            select(UsageLog.run_id, UsageLog.bot_id, func.sum(UsageLog.input_tokens + UsageLog.output_tokens))
            .where(UsageLog.user_id == user.id, UsageLog.run_id.in_(run_ids), UsageLog.task_id.is_(None))
            .group_by(UsageLog.run_id, UsageLog.bot_id)
        ).all()
    } if run_ids else {}
    approval_ids = {(m.meta or {}).get("approval_id") for m in rows if m.type == "approval"} - {None}
    approval_map = {a.id: a for a in db.scalars(select(Approval).where(Approval.id.in_(approval_ids)))} if approval_ids else {}
    question_ids = {(m.meta or {}).get("question_id") for m in rows if m.type == "question"} - {None}
    question_map = {q.id: q for q in db.scalars(select(Question).where(Question.id.in_(question_ids)))} if question_ids else {}
    for m in rows:
        data = message_out(m, sender_bots.get(m.sender_bot_id or ""))
        if m.id in live_content:
            data["content"] = live_content[m.id]
        if m.sender_type == "bot" and not (m.meta or {}).get("task_id") and (m.run_id, m.sender_bot_id) in agent_usage:
            data["meta"] = {**data["meta"], "agent_tokens": agent_usage[(m.run_id, m.sender_bot_id)]}
        if m.run_id in run_map:
            data["meta"] = {**data["meta"], "run_status": run_map[m.run_id].status}
            if m.type == "plan" and data["meta"].get("status") == "pending" and run_map[m.run_id].status in ("completed", "failed", "stopped"):
                data["meta"]["status"] = "canceled"
        if m.type == "approval":
            ap = approval_map.get((m.meta or {}).get("approval_id"))
            run = run_map.get(m.run_id)
            status = ap.status if ap else data["meta"].get("status")
            if status == "pending" and run and run.status in ("completed", "failed", "stopped"):
                status = "invalidated"
            data["meta"] = {**data["meta"], "status": status}
            if status == "invalidated":
                data["meta"]["closed_reason"] = run.end_reason if run else "所属执行已结束"
        if m.type == "question":
            question = question_map.get((m.meta or {}).get("question_id"))
            run = run_map.get(m.run_id)
            status = question.status if question else data["meta"].get("status")
            if status == "pending" and run and run.status in ("completed", "failed", "stopped"):
                status = "expired"
            data["meta"] = {**data["meta"], "status": status}
            if question and status == "answered":
                data["meta"]["answer"] = question.answer
            elif status == "expired":
                data["meta"]["closed_reason"] = run.end_reason if run else "问题已过期"
        task = task_map.get((m.meta or {}).get("task_id"))
        if task:
            data["meta"] = {**data["meta"], "task_status": task.status}
        serialized.append(data)
    return {
        "messages": serialized,
        "has_more": has_more,
        "event_cursor": event_cursor,
    }


@router.get("/{chat_id}/messages/search")
def search_messages(
    chat_id: str,
    q: str = Query(min_length=1, max_length=100),
    before: int | None = Query(default=None),
    limit: int = Query(default=20, ge=1, le=50),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    """CHT-08：在当前群聊的主线程里按正文做全文搜索。

    只搜主线程——聊天页展示的就是主线程，子线程结果跳不过去。匹配用
    `ILIKE '%q%'`（转义通配符），Postgres 下走 pg_trgm 索引；按 seq 倒序
    返回，翻页用 `before=<上一页最后一条的 seq>`。
    """
    chat = owned_or_404(db, Chat, chat_id, user.id)
    term = q.strip()
    main = db.scalar(select(Thread).where(Thread.chat_id == chat.id, Thread.type == "main"))
    if not term or not main:
        return {"results": [], "total": 0, "has_more": False, "query": term}

    match = (Message.thread_id == main.id, Message.content.icontains(term, autoescape=True))
    total = db.scalar(select(func.count()).select_from(Message).where(*match)) or 0

    page = select(Message).where(*match)
    if before:
        page = page.where(Message.seq < before)
    rows = list(db.scalars(page.order_by(Message.seq.desc()).limit(limit + 1)).all())
    has_more = len(rows) > limit
    rows = rows[:limit]

    sender_ids = {m.sender_bot_id for m in rows if m.sender_bot_id}
    sender_bots = {
        b.id: b for b in db.scalars(select(Bot).where(Bot.id.in_(sender_ids))).all()
    } if sender_ids else {}

    results = []
    for m in rows:
        bot = sender_bots.get(m.sender_bot_id or "")
        hit = {
            "id": m.id,
            "seq": m.seq,
            "type": m.type,
            "sender_type": m.sender_type,
            "sender_bot_id": m.sender_bot_id,
            "snippet": _snippet(m.content, term),
            "created_at": iso(m.created_at),
        }
        if bot is not None:
            hit["sender_bot_name"] = bot.name if not bot.deleted_at else f"{bot.name}（已删除）"
            hit["sender_bot_avatar"] = bot.avatar
        results.append(hit)
    return {"results": results, "total": total, "has_more": has_more, "query": term}


def _snippet(content: str, term: str, radius: int = 60) -> str:
    """截取命中词前后的一段文本；折叠换行，避免结果列表被长文撑开。"""
    text = " ".join((content or "").split())
    idx = text.lower().find(term.lower())
    if idx < 0:
        # 内容里没找到（例如只命中转义后的形式）：退回开头一段
        return text[: radius * 2] + ("…" if len(text) > radius * 2 else "")
    start = max(0, idx - radius)
    end = min(len(text), idx + len(term) + radius)
    return ("…" if start else "") + text[start:end] + ("…" if end < len(text) else "")


@router.get("/{chat_id}/export")
def export_chat(
    chat_id: str,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    """15.2：把主线程导出为 Markdown。

    只导出对话本身：用户消息、Bot 的最终回复、计划卡片与系统提示。子 Bot 的
    过程镜像（`subtask_live`）、工具调用与内部派任务副本不导出，否则同一段内容
    会在记录里出现多次。时间统一按北京时间（UTC+8）呈现。
    """
    chat = owned_or_404(db, Chat, chat_id, user.id)
    main = db.scalar(select(Thread).where(Thread.chat_id == chat.id, Thread.type == "main"))
    if not main:
        raise HTTPException(404, "群聊没有消息")
    msgs = db.scalars(
        select(Message).where(Message.thread_id == main.id).order_by(Message.seq)
    ).all()
    member_ids = db.scalars(select(ChatMember.bot_id).where(ChatMember.chat_id == chat.id)).all()
    bots = {
        b.id: b
        for b in db.scalars(select(Bot).where(Bot.user_id == user.id, Bot.id.in_(member_ids))).all()
    } if member_ids else {}
    # 已移出群聊的 Bot 也要能正确署名
    sender_ids = {m.sender_bot_id for m in msgs if m.sender_bot_id} - set(bots)
    if sender_ids:
        for b in db.scalars(select(Bot).where(Bot.id.in_(sender_ids))).all():
            bots[b.id] = b

    def local(value: datetime) -> datetime:
        return value.replace(tzinfo=timezone.utc).astimezone(timezone(timedelta(hours=8)))

    def name_of(m: Message) -> str:
        if m.sender_type == "user":
            return "用户"
        if m.sender_type == "system":
            return "系统"
        bot = bots.get(m.sender_bot_id or "")
        return (bot.name if bot else None) or (m.meta or {}).get("bot_name") or "Bot"

    now_local = local(now())
    lines: list[str] = [
        f"# {chat.name}",
        "",
        f"- 导出时间：{now_local:%Y-%m-%d %H:%M}（北京时间）",
        f"- 成员：{'、'.join(b.name for b in bots.values() if not b.deleted_at) or '无'}",
        "",
        "---",
        "",
    ]
    day = None
    count = 0
    for m in msgs:
        if m.status == "streaming":
            continue
        meta = m.meta or {}
        body = ""
        if m.type == "text":
            if meta.get("subtask_live") or meta.get("delegation"):
                continue
            body = (m.content or "").strip()
        elif m.type == "plan":
            steps = [s for s in (meta.get("steps") or []) if isinstance(s, dict)]
            status = {"pending": "待确认", "approved": "已确认", "canceled": "已取消"}.get(
                str(meta.get("status") or ""), str(meta.get("status") or "")
            )
            body = "**计划步骤**" + (f"（{status}）" if status else "")
            for i, s in enumerate(steps, 1):
                title = s.get("title") or s.get("instruction") or ""
                owner = s.get("bot") or ""
                body += f"\n{i}. {title}" + (f" —— {owner}" if owner else "")
        elif m.type in ("system", "limit"):
            body = f"> {m.content}"
        elif m.type == "approval":
            status = str(meta.get("status") or "")
            body = f"> 审批请求：{meta.get('tool') or m.content}（{status or '待处理'}）"
        elif m.type == "question":
            status = str(meta.get("status") or "")
            body = f"> 提问：{m.content}（{status or '待回复'}）"
            if meta.get("answer"):
                body += f"\n>\n> 回答：{meta['answer']}"
        elif m.type == "task" and (m.content or "").strip():
            body = f"> 任务：{m.content}"
        elif m.type == "task_done" and (m.content or "").strip():
            state = str(meta.get("status") or "")
            label = "任务失败" if state == "failed" else "任务完成"
            info = []
            if meta.get("bot_name"):
                info.append(str(meta["bot_name"]))
            if meta.get("elapsed"):
                info.append(f"耗时 {meta['elapsed']}")
            if meta.get("tokens"):
                info.append(f"{meta['tokens']} tokens")
            prefix = f"（{' · '.join(info)}）" if info else ""
            body = f"> {label}{prefix}：{m.content}"
        elif m.type == "handoff" and (m.content or "").strip():
            body = f"> {m.content}"
        if not body.strip():
            continue

        stamp = local(m.created_at)
        if stamp.date() != day:
            day = stamp.date()
            lines += [f"## {day:%Y-%m-%d}", ""]
        lines += [f"**{name_of(m)}** · {stamp:%H:%M}", "", body, ""]
        count += 1

    if not count:
        lines += ["（暂无消息）", ""]

    filename = re.sub(r'[\\/:*?"<>|\s]+', "-", chat.name).strip("-") or "chat"
    filename = f"{filename}-{now_local:%Y%m%d}.md"
    headers = {
        "Content-Disposition": (
            f'attachment; filename="chat-{now_local:%Y%m%d}.md"; '
            f"filename*=UTF-8''{quote(filename)}"
        ),
        "X-Content-Type-Options": "nosniff",
        "Cache-Control": "no-store",
    }
    return Response("\n".join(lines), media_type="text/markdown; charset=utf-8", headers=headers)


@router.get("/{chat_id}/message-target")
def message_target(
    chat_id: str, kind: str, target_id: str,
    user: User = Depends(current_user), db: Session = Depends(get_db),
):
    chat = owned_or_404(db, Chat, chat_id, user.id)
    main = db.scalar(select(Thread).where(Thread.chat_id == chat.id, Thread.type == "main"))
    if not main:
        raise HTTPException(404, "群聊没有消息")
    task_id = None
    if kind in ("approval", "question"):
        record = owned_or_404(db, Approval if kind == "approval" else Question, target_id, user.id)
        run = db.get(Run, record.run_id)
        if not run or run.chat_id != chat.id:
            raise HTTPException(404, "消息不存在")
        msg = db.scalar(select(Message).where(Message.thread_id == main.id, Message.type == kind,
                        Message.meta[f"{kind}_id"].as_string() == target_id).order_by(Message.seq.desc()))
    elif kind == "run":
        run = owned_or_404(db, Run, target_id, user.id)
        if run.chat_id != chat.id:
            raise HTTPException(404, "执行不属于该群聊")
        msg = db.scalar(select(Message).where(Message.thread_id == main.id, Message.run_id == run.id,
                        Message.type.in_(["text", "system", "limit", "plan", "approval", "question"]))
                        .order_by(Message.seq.desc()))
    elif kind == "task":
        task = owned_or_404(db, Task, target_id, user.id)
        if db.get(Thread, task.thread_id).chat_id != chat.id:
            raise HTTPException(404, "任务不属于该群聊")
        task_id = task.id
        msg = db.scalar(select(Message).where(Message.thread_id == main.id,
                        Message.meta["task_id"].as_string() == target_id).order_by(Message.seq.desc()))
    else:
        raise HTTPException(400, "不支持的消息定位类型")
    if not msg:
        raise HTTPException(404, "对应消息已不存在")
    return {"message_id": msg.id, "task_id": task_id}


def _resolve_mention(db: Session, chat_id: str, content: str, explicit: str | None) -> Bot | None:
    members = _members(db, chat_id)
    if explicit:
        return next((bot for bot in members if bot.id == explicit), None)
    for b in members:
        # 允许「@研究员 你好」「@研究员，帮我查」这类中文标点紧邻的写法
        if re.search(rf"@{re.escape(b.name)}(?=[\s，。！？、；：,.!?;:]|$)", content):
            return b
    return None


def _post_free_message(db: Session, chat: Chat, main: Thread, payload: MessageIn, content: str):
    members = [b for b in _members(db, chat.id) if b.deleted_at is None]
    ids = list(dict.fromkeys([*([payload.mention_bot_id] if payload.mention_bot_id else []),
                             *payload.mention_bot_ids, *mentioned_members(content, members)]))
    everyone = payload.everyone or bool(re.search(r"@(?:全员|everyone)(?=[\s，。！？、；：,.!?;:]|$)", content))
    reply = None
    if payload.reply_to:
        reply = owned_or_404(db, Message, payload.reply_to, chat.user_id)
        if reply.thread_id != main.id or reply.sender_type != "bot" or not reply.sender_bot_id:
            raise HTTPException(400, "只能回复本群主线程中的成员消息")
        if not ids and not everyone:
            ids = [reply.sender_bot_id]
    by_id = {b.id: b for b in members}
    if any(bid not in by_id for bid in ids):
        raise HTTPException(404, "该群聊中不存在指定的 Bot")
    for bid in ids:
        if by_id[bid].enabled is False or not by_id[bid].provider_id:
            raise HTTPException(409, f"Bot「{by_id[bid].name}」已停用或尚未配置模型")
    if not everyone and len(ids) > chat.max_speakers:
        raise HTTPException(400, "@ 的成员数超过本群发言人数上限，请在群聊设置中调整或使用 @全员")
    available = [b for b in members if b.enabled is not False and b.provider_id]
    if not available and (everyone or ids or not chat.muted):
        raise HTTPException(409, "请至少为一位启用的成员配置模型")
    msg = add_message(db, thread=main, run_id=None, sender_type="user", type_="text", content=content,
                      meta={"reply_to": reply.id if reply else None, "mention_bot_ids": ids, "everyone": everyone})
    chat.last_active_at = now()
    if chat.muted and not ids and not everyone:
        db.commit()
        return {"message": message_out(msg), "run": None}
    run = Run(user_id=chat.user_id, chat_id=chat.id, kind="free", trigger_message_id=msg.id,
              status="queued", mode=chat.run_mode, config_snapshot={
                  "chat_mode": "free", "run_mode": chat.run_mode,
                  "supervisor": {"id": chat.supervisor_bot_id} if chat.supervisor_bot_id else None,
                  "members": [{"id": b.id, "name": b.name} for b in available],
                  "bot_configs": capture_bots(db, available), "limits": chat.limits_override or {},
                  "free": {"targets": [b.id for b in available] if everyone else ids,
                           "parallel": True,
                           "everyone": everyone, "directed": bool(ids) or everyone,
                           "max_speakers": len(available) if everyone else chat.max_speakers,
                           "max_rounds": 1 if everyone else chat.max_rounds,
                           "spoken": [], "queue": [], "turn": 0},
              })
    db.add(run)
    db.flush()
    run.config_snapshot = {**run.config_snapshot, "task_workspace": f"tasks/{run.id}"}
    msg.run_id = run.id
    emit_run_status(db, run)
    db.commit()
    enqueue(run.id)
    return {"message": message_out(msg), "run": run_out(run)}


@router.post("/{chat_id}/messages", status_code=201)
def post_message(
        chat_id: str,
        payload: MessageIn,
        user: User = Depends(current_user),
        db: Session = Depends(get_db),
):
    # 与进行中执行检查使用同一事务，避免两个标签同时发消息创建双重执行。
    if IS_SQLITE:
        db.connection().exec_driver_sql("BEGIN IMMEDIATE")
    chat = db.scalar(select(Chat).where(Chat.id == chat_id, Chat.user_id == user.id).with_for_update())
    if not chat:
        raise HTTPException(404, "群聊不存在")
    content = payload.content.strip()
    if not content:
        raise HTTPException(400, "消息不能为空")

    active = _active_run(db, chat.id)
    terminal_busy = db.scalar(select(TerminalCommand.id).where(TerminalCommand.user_id == user.id,
        TerminalCommand.chat_id == chat.id, TerminalCommand.bot_id.is_(None),
        TerminalCommand.status.in_(("queued", "running"))).limit(1))
    if terminal_busy:
        raise HTTPException(409, "终端命令正在执行，请等待完成后发起任务")
    # CHT-07：分支线程里的消息单独触发执行，上下文与回复都落在分支，不打断主线程
    if payload.thread_id:
        if active:
            raise HTTPException(409, "当前群聊已有执行正在进行，请先停止或等待完成")
        branch = owned_or_404(db, Thread, payload.thread_id, user.id)
        if branch.chat_id != chat.id or branch.type != "branch":
            raise HTTPException(404, "分支不存在")
        sup = db.get(Bot, chat.supervisor_bot_id) if chat.supervisor_bot_id else None
        if not sup:
            raise HTTPException(400, "该群聊没有配置总指挥 Bot")
        if not sup.provider_id:
            raise HTTPException(
                409, f"Bot「{sup.name}」尚未绑定模型接入，请先到设置页配置模型接入"
            )
        msg = add_message(
            db, thread=branch, run_id=None, sender_type="user", type_="text", content=content
        )
        chat.last_active_at = now()
        run = Run(
            user_id=user.id,
            chat_id=chat.id,
            trigger_message_id=msg.id,
            status="queued",
            mode=chat.run_mode,
            thread_id=branch.id,
            config_snapshot={
                "run_mode": chat.run_mode,
                "supervisor": {"id": sup.id, "name": sup.name},
                "members": [{"id": m.id, "name": m.name} for m in _members(db, chat.id)],
                "bot_configs": capture_bots(db, _members(db, chat.id)),
                "limits": chat.limits_override or {},
                "branch": {"id": branch.id, "title": branch.title},
            },
        )
        db.add(run)
        db.flush()
        msg.run_id = run.id
        db.flush()
        emit(
            db,
            user.id,
            chat.id,
            "run.status",
            {"run_id": run.id, "chat_id": chat.id, "status": "queued", "steps_used": 0, "tokens_used": 0},
        )
        db.commit()
        enqueue(run.id)
        return {"message": message_out(msg), "run": run_out(run)}

    if active and chat.mode != "free":
        if not payload.interject:
            raise HTTPException(409, "当前群聊已有执行正在进行，请先停止或等待完成")
        # RUN-13：不新建执行，消息作为「执行中补充」进入当前执行的线程。
        # 分支执行时写分支线程，主线程执行时才写主线程。
        thread = (db.get(Thread, active.thread_id)
                  if active.thread_id and not (active.config_snapshot or {}).get("retry_task_id")
                  else _main_thread(db, chat))
        msg = add_message(
            db,
            thread=thread,
            run_id=active.id,
            sender_type="user",
            type_="text",
            content=content,
            meta={"kind": "interjection"},
        )
        chat.last_active_at = now()
        db.flush()
        db.commit()
        return {"message": message_out(msg), "run": run_out(active), "interjection": True}

    main = db.scalar(select(Thread).where(Thread.chat_id == chat.id, Thread.type == "main"))
    if not main:
        main = Thread(user_id=user.id, chat_id=chat.id, type="main", title="主线程")
        db.add(main)
        db.flush()

    if chat.mode == "free":
        return _post_free_message(db, chat, main, payload, content)
    target = _resolve_mention(db, chat.id, content, payload.mention_bot_id)
    if payload.mention_bot_id and not target:
        raise HTTPException(404, "该群聊中不存在指定的 Bot")
    sup = db.get(Bot, chat.supervisor_bot_id) if chat.supervisor_bot_id else None
    effective = target or sup
    if not effective:
        raise HTTPException(400, "该群聊没有配置总指挥 Bot")
    # BOT-06：停用的 Bot 不再参与新执行，先启用或换人
    if effective.enabled is False:
        who = "该成员" if target else "总指挥"
        raise HTTPException(
            409,
            f"{who}「{effective.name}」已停用，请先到 Bot 页启用"
            + ("，或换一位总指挥" if not target else ""),
        )
    if not effective.provider_id:
        raise HTTPException(
            409, f"Bot「{effective.name}」尚未绑定模型接入，请先到设置页配置模型接入"
        )

    msg = add_message(
        db,
        thread=main,
        run_id=None,
        sender_type="user",
        type_="text",
        content=content,
    )
    chat.last_active_at = now()
    run = Run(
        user_id=user.id,
        chat_id=chat.id,
        trigger_message_id=msg.id,
        status="queued",
        target_bot_id=target.id if target else None,
        mode=chat.run_mode,
        config_snapshot={
            "run_mode": chat.run_mode,
            "supervisor": {"id": sup.id, "name": sup.name} if sup else None,
            "members": [{"id": m.id, "name": m.name} for m in _members(db, chat.id)],
            "bot_configs": capture_bots(db, _members(db, chat.id)),
            "limits": chat.limits_override or {},
        },
    )
    db.add(run)
    db.flush()
    # 回填 run_id，便于前端把用户消息与执行关联
    msg.run_id = run.id
    db.flush()
    emit(
        db,
        user.id,
        chat.id,
        "run.status",
        {"run_id": run.id, "chat_id": chat.id, "status": "queued", "steps_used": 0, "tokens_used": 0},
    )
    # 先落库再入队，避免 Worker 读到未提交的执行
    db.commit()
    enqueue(run.id)
    return {"message": message_out(msg), "run": run_out(run)}


@router.post("/{chat_id}/tasks/{task_id}/retry", status_code=201)
def retry_task(
    chat_id: str, task_id: str, payload: RetryTaskIn,
    user: User = Depends(current_user), db: Session = Depends(get_db),
):
    if IS_SQLITE:
        db.connection().exec_driver_sql("BEGIN IMMEDIATE")
    chat = db.scalar(select(Chat).where(Chat.id == chat_id, Chat.user_id == user.id).with_for_update())
    if not chat:
        raise HTTPException(404, "群聊不存在")
    original = owned_or_404(db, Task, task_id, user.id)
    source = owned_or_404(db, Run, original.run_id, user.id)
    if source.chat_id != chat.id:
        raise HTTPException(404, "任务不存在")
    if original.status not in ("failed", "canceled"):
        raise HTTPException(409, "只能重试失败或已取消的任务")
    if _active_run(db, chat.id):
        raise HTTPException(409, "当前群聊仍在执行，请等待完成后重试此任务")
    if db.scalar(select(TerminalCommand.id).where(
        TerminalCommand.chat_id == chat.id, TerminalCommand.status.in_(("queued", "running")),
    ).limit(1)):
        raise HTTPException(409, "终端命令正在执行，请等待完成后重试")
    members = _members(db, chat.id)
    bot = next((b for b in members if b.id == original.assigned_bot_id and not b.deleted_at), None)
    if not bot or bot.enabled is False:
        raise HTTPException(409, "原执行成员已移出或停用，请先恢复该成员")
    provider = db.get(Provider, bot.provider_id) if bot.provider_id else None
    if not provider or provider.user_id != user.id:
        raise HTTPException(409, "原执行成员尚未配置可用模型，请先到智能体页面配置")
    main = _main_thread(db, chat)
    original_thread = db.get(Thread, original.thread_id)
    if not original_thread:
        raise HTTPException(409, "原任务线程已不可用，无法重试")
    updates = RunExecutor(source.id)._interjection_messages(db, source, original_thread)
    instruction = original.instruction
    if updates:
        instruction += "\n\n原执行中的用户补充要求：\n" + "\n".join(m.content for m in updates)
    if payload.note.strip():
        instruction += "\n\n本次重试补充：\n" + payload.note.strip()
    instruction += "\n\n只完成这项任务，保留并复用已有资料和产物，按本次实际执行结果判断，不重跑其他已完成任务。"
    from app.sandbox.workspace import relative_workspace
    workspace_source = db.scalar(select(Run).where(Run.parent_task_id == original.id).order_by(Run.created_at.desc())) or source
    run = Run(user_id=user.id, chat_id=chat.id, target_bot_id=bot.id, status="queued", mode="auto",
              config_snapshot={"run_mode": "auto", "supervisor": {"id": chat.supervisor_bot_id},
                  "members": [{"id": b.id, "name": b.name} for b in members], "bot_configs": capture_bots(db, members),
                  "limits": chat.limits_override or {}, "retry_of_task_id": original.id,
                  "task_workspace": relative_workspace(workspace_source)})
    db.add(run)
    db.flush()
    thread = Thread(user_id=user.id, chat_id=chat.id, parent_thread_id=main.id, type="task",
                    title=f"重试：{original.instruction[:50]}", bot_id=bot.id)
    db.add(thread)
    db.flush()
    task = Task(user_id=user.id, run_id=run.id, thread_id=thread.id, assigned_bot_id=bot.id,
                instruction=instruction, expected_output=original.expected_output, step_key=original.step_key,
                attempt=original.attempt + 1, status="queued")
    db.add(task)
    db.flush()
    run.thread_id = thread.id
    run.config_snapshot = {**run.config_snapshot, "retry_task_id": task.id}
    message = add_message(db, thread=main, run_id=run.id, sender_type="user", type_="text",
        content=f"只重试「{bot.name}」的任务：{original.instruction}" + (f"\n\n补充：{payload.note.strip()}" if payload.note.strip() else ""),
        meta={"retry_of_task_id": original.id, "task_id": task.id})
    run.trigger_message_id = message.id
    chat.last_active_at = now()
    emit_task_status(db, task)
    emit_run_status(db, run)
    db.commit()
    enqueue(run.id)
    return {"message": message_out(message), "run": run_out(run), "task": task_out(task, bot)}


@router.get("/{chat_id}/files")
def list_files(chat_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    chat = owned_or_404(db, Chat, chat_id, user.id)
    rows = visible_files(db.scalars(
        select(FileRec).where(FileRec.chat_id == chat.id).order_by(FileRec.path)
    ).all())
    return [
        {
            "id": f.id,
            "path": f.path,
            "size": f.size,
            "run_id": f.run_id,
            "created_by_bot_id": f.created_by_bot_id,
            "updated_at": f.updated_at.isoformat() if f.updated_at else None,
        }
        for f in rows
    ]


@router.post("/{chat_id}/files", status_code=201)
async def upload_file(
    chat_id: str, file: UploadFile = File(...),
    user: User = Depends(current_user), db: Session = Depends(get_db),
):
    chat = owned_or_404(db, Chat, chat_id, user.id)
    name = (file.filename or "").strip()
    if not name or name in (".", "..") or "/" in name or "\\" in name or any(ord(c) < 32 for c in name) or len(name.encode("utf-8")) > 220:
        await file.close()
        raise HTTPException(400, "文件名无效，请使用不含目录的普通文件名")
    try:
        content = await file.read(SANDBOX_FILE_MAX_MB * 1024 * 1024 + 1)
    finally:
        await file.close()
    if len(content) > SANDBOX_FILE_MAX_MB * 1024 * 1024:
        raise HTTPException(413, f"单文件不能超过 {SANDBOX_FILE_MAX_MB} MB")
    try:
        if sandbox.safe_path(user.id, chat.id, name).exists():
            path = PurePosixPath(name)
            name = f"{path.stem}-{uuid4().hex[:8]}{path.suffix}"
        size = sandbox.write_bytes(user.id, chat.id, name, content)
    except sandbox.SandboxError as exc:
        raise HTTPException(400, str(exc)) from exc
    rec = FileRec(user_id=user.id, chat_id=chat.id, path=name, size=size)
    db.add(rec)
    try:
        db.flush()
        emit(db, user.id, chat.id, "files.updated", {"chat_id": chat.id})
        db.commit()
    except Exception:
        sandbox.delete_file(user.id, chat.id, name)
        raise
    return {"id": rec.id, "path": rec.path, "size": rec.size,
            "updated_at": rec.updated_at.isoformat() if rec.updated_at else None}
