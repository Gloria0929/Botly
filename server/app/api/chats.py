"""群聊、消息与执行触发。"""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from pathlib import PurePosixPath
from urllib.parse import quote
from uuid import uuid4

from fastapi import APIRouter, Depends, File, HTTPException, Query, Response, UploadFile
from pydantic import BaseModel
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from .. import sandbox
from ..capabilities import supervisor_block_reason
from ..db import IS_SQLITE, get_db
from ..config import SANDBOX_FILE_MAX_MB
from ..deps import current_user, owned_or_404
from ..events import emit
from ..execution_config import capture_bots
from ..graph import open_checkpointer
from ..models import (Approval, Bot, Chat, ChatMember, Event, FileRec, Message, Notification, Provider,
                      Question, Run, Task, Thread, UsageLog, User, UserSettings, now)
from ..orchestrator import add_message
from ..redis_bus import enqueue
from ..serializers import bot_out, chat_out, iso, message_out, run_out, task_out

router = APIRouter(prefix="/chats", tags=["chats"])

ACTIVE = ("queued", "running", "waiting")


class ChatIn(BaseModel):
    name: str
    member_ids: list[str] = []
    supervisor_bot_id: str | None = None


class ChatPatch(BaseModel):
    name: str | None = None
    supervisor_bot_id: str | None = None
    member_ids: list[str] | None = None
    run_mode: str | None = None
    limits_override: dict | None = None
    always_allow_tools: list[str] | None = None
    require_approval_for_write: bool | None = None


class MessageIn(BaseModel):
    content: str
    mention_bot_id: str | None = None
    # 执行中补充（RUN-13）：为 True 时不新建执行，只把消息交给正在跑的执行
    interject: bool = False


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
    return db.scalar(
        select(Run)
        .where(Run.chat_id == chat_id, Run.status.in_(ACTIVE))
        .order_by(Run.created_at.desc())
    )


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
    out = []
    for c in chats:
        active = _active_run(db, c.id)
        last = db.scalar(
            select(Message)
            .join(Thread, Thread.id == Message.thread_id)
            .where(Thread.chat_id == c.id, Message.type == "text")
            .order_by(Message.created_at.desc())
        )
        out.append(
            {
                "id": c.id,
                "name": c.name,
                "run_mode": c.run_mode,
                "last_active_at": c.last_active_at.isoformat() if c.last_active_at else None,
                "run_status": active.status if active else None,
                "preview": (last.content or "")[:60] if last else "",
                "member_count": db.scalar(
                    select(func.count(ChatMember.bot_id)).where(ChatMember.chat_id == c.id)
                ),
            }
        )
    return out


@router.post("", status_code=201)
def create_chat(payload: ChatIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
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
        return supervisor_block_reason(bot, providers.get(bot.provider_id or ""))

    sup_id = payload.supervisor_bot_id
    if sup_id and sup_id in by_id:
        # MOD-02：模型明确不支持工具调用 / 流式输出时，不允许当总指挥
        reason = _blocked(sup_id)
        if reason:
            raise HTTPException(400, reason)
    else:
        # 自动挑选：优先总指挥模板，跳过明确不能用的模型；都不合格时退回第一个成员
        candidates = [b.id for b in bots if b.is_supervisor_template] + member_ids
        sup_id = next((bid for bid in candidates if not _blocked(bid)), member_ids[0])
    if sup_id not in member_ids:
        member_ids.insert(0, sup_id)

    chat = Chat(user_id=user.id, name=payload.name.strip() or "新群聊", supervisor_bot_id=sup_id)
    db.add(chat)
    db.flush()
    for bid in member_ids:
        db.add(ChatMember(chat_id=chat.id, bot_id=bid, user_id=user.id))
    db.add(Thread(user_id=user.id, chat_id=chat.id, type="main", title="主线程"))
    db.flush()
    return chat_out(chat, by_id.get(sup_id), _members(db, chat.id))


@router.get("/{chat_id}")
def get_chat(chat_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    chat = owned_or_404(db, Chat, chat_id, user.id)
    members = _members(db, chat.id)
    sup = db.get(Bot, chat.supervisor_bot_id) if chat.supervisor_bot_id else None
    active = _active_run(db, chat.id)
    tasks = []
    status_map: dict[str, str] = {}
    # 没有进行中的执行时，回退到最近一次有任务的执行，保证任务面板不会在完成后清空
    panel_run = active
    if not panel_run:
        panel_run = db.scalar(
            select(Run)
            .where(Run.chat_id == chat.id, select(Task.id).where(Task.run_id == Run.id).exists())
            .order_by(Run.created_at.desc())
        )
    if panel_run:
        rows = db.scalars(
            select(Task).where(Task.run_id == panel_run.id).order_by(Task.created_at)
        ).all()
        for t in rows:
            tasks.append(task_out(t, db.get(Bot, t.assigned_bot_id)))
            if active and t.status == "running":
                status_map[t.assigned_bot_id] = "working"
            elif active and t.status in ("waiting_approval", "waiting_reply"):
                status_map[t.assigned_bot_id] = "waiting"
    if active:
        supervisor = ((active.config_snapshot or {}).get("supervisor") or {}).get("id") or chat.supervisor_bot_id
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
    data["members"] = [
        {**bot_out(m), "status": status_map.get(m.id, "idle")} for m in members
    ]
    data["active_run"] = run_out(active) if active else None
    latest_run = active or db.scalar(select(Run).where(Run.chat_id == chat.id).order_by(Run.created_at.desc()))
    data["last_run"] = run_out(latest_run) if latest_run else None
    data["tasks"] = tasks
    pending = db.scalars(select(Approval).join(Run, Run.id == Approval.run_id).where(
        Run.chat_id == chat.id, Run.status.in_(ACTIVE), Approval.status == "pending",
    ).order_by(Approval.created_at)).all()
    data["pending_approvals"] = [{"id": a.id, "tool": a.tool,
        "bot_name": db.get(Bot, a.bot_id).name if db.get(Bot, a.bot_id) else "Bot"} for a in pending]
    data["files"] = [
        {"id": f.id, "path": f.path, "size": f.size, "updated_at": f.updated_at.isoformat()}
        for f in db.scalars(
            select(FileRec).where(FileRec.chat_id == chat.id).order_by(FileRec.path)
        ).all()
    ]
    settings = db.get(UserSettings, user.id)
    data["limits"] = {
        "max_steps": 0,
        "token_budget": (chat.limits_override or {}).get("token_budget")
                        if (chat.limits_override or {}).get("token_budget") is not None
                        else (settings.token_budget if settings else 300000),
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
    if "limits_override" in data:
        override = data["limits_override"]
        if not isinstance(override, dict):
            raise HTTPException(400, "限额覆盖必须是对象")
        unknown = set(override) - {"token_budget"}
        if unknown:
            raise HTTPException(400, f"不支持的限额项：{'、'.join(sorted(unknown))}")
        budget = override.get("token_budget")
        if budget is not None and (type(budget) is not int or (budget != 0 and budget < 1000)):
            raise HTTPException(400, "token 预算应为 0（无限制）或至少 1000")
    if "run_mode" in data and data["run_mode"] not in ("auto", "step"):
        raise HTTPException(400, "运行模式只能是 auto 或 step")

    old_supervisor = chat.supervisor_bot_id
    new_supervisor = data.get("supervisor_bot_id", old_supervisor)
    if "supervisor_bot_id" in data:
        if not new_supervisor:
            raise HTTPException(400, "群聊必须有总指挥，请指定一个成员 Bot")
        sup_bot = owned_or_404(db, Bot, new_supervisor, user.id)
        if sup_bot.deleted_at:
            raise HTTPException(400, "该 Bot 已被删除，不能担任总指挥")
        # MOD-02：同上，模型明确不支持工具调用 / 流式输出时不接受为新总指挥
        provider = db.get(Provider, sup_bot.provider_id) if sup_bot.provider_id else None
        reason = supervisor_block_reason(sup_bot, provider)
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
    db.flush()

    db.delete(chat)
    db.flush()

    # 沙箱文件在磁盘上，随群聊一起清掉
    try:
        sandbox.delete_chat_dir(user.id, chat.id)
    except sandbox.SandboxError:
        pass
    return {"ok": True}


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
    approval_ids = {(m.meta or {}).get("approval_id") for m in rows if m.type == "approval"} - {None}
    approval_map = {a.id: a for a in db.scalars(select(Approval).where(Approval.id.in_(approval_ids)))} if approval_ids else {}
    question_ids = {(m.meta or {}).get("question_id") for m in rows if m.type == "question"} - {None}
    question_map = {q.id: q for q in db.scalars(select(Question).where(Question.id.in_(question_ids)))} if question_ids else {}
    for m in rows:
        data = message_out(m, sender_bots.get(m.sender_bot_id or ""))
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
    过程镜像（`subtask_live`）、工具调用与内部派活副本不导出，否则同一段内容
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
            body = f"> 任务完成：{m.content}"
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
    if active:
        if not payload.interject:
            raise HTTPException(409, "当前群聊已有执行正在进行，请先停止或等待完成")
        # RUN-13：不新建执行，消息作为「执行中补充」进入主线程。
        # 总指挥在下一个模型调用前读到它（见 orchestrator._build_messages）。
        main = _main_thread(db, chat)
        msg = add_message(
            db,
            thread=main,
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

    target = _resolve_mention(db, chat.id, content, payload.mention_bot_id)
    if payload.mention_bot_id and not target:
        raise HTTPException(404, "该群聊中不存在指定的 Bot")
    sup = db.get(Bot, chat.supervisor_bot_id) if chat.supervisor_bot_id else None
    effective = target or sup
    if not effective:
        raise HTTPException(400, "该群聊没有配置总指挥 Bot")
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


@router.get("/{chat_id}/files")
def list_files(chat_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    chat = owned_or_404(db, Chat, chat_id, user.id)
    rows = db.scalars(
        select(FileRec).where(FileRec.chat_id == chat.id).order_by(FileRec.path)
    ).all()
    return [
        {
            "id": f.id,
            "path": f.path,
            "size": f.size,
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
