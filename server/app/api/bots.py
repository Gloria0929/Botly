"""Bot 管理。"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..db import get_db
from ..deps import current_user, owned_or_404
from ..models import Bot, Chat, ChatMember, Provider, Run, Task, User, now
from ..serializers import bot_out
from ..templates import BOT_TEMPLATES, out as template_out
from ..tools import BUILTIN_TOOLS

router = APIRouter(prefix="/bots", tags=["bots"])

ALL_TOOLS = list(BUILTIN_TOOLS.keys())


class BotIn(BaseModel):
    name: str = Field(min_length=1, max_length=40)
    avatar: str = "🤖"
    role: str = "通用助手"
    system_prompt: str = ""
    provider_id: str | None = None
    model: str | None = None
    temperature: float = 0.4
    tools: list[str] = []


class BotPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=40)
    avatar: str | None = None
    role: str | None = None
    system_prompt: str | None = None
    provider_id: str | None = None
    model: str | None = None
    temperature: float | None = None
    tools: list[str] | None = None


def _validate(bot_in: dict, user_id: str, db: Session, exclude_id: str | None = None):
    name = bot_in.get("name")
    if name:
        q = select(Bot).where(
            Bot.user_id == user_id, Bot.name == name, Bot.deleted_at.is_(None)
        )
        if exclude_id:
            q = q.where(Bot.id != exclude_id)
        if db.scalar(q):
            raise HTTPException(400, f"已存在名为「{name}」的 Bot")
    tools = bot_in.get("tools")
    if tools is not None:
        unknown = [t for t in tools if t not in ALL_TOOLS]
        if unknown:
            raise HTTPException(400, f"未知工具：{'、'.join(unknown)}")
    pid = bot_in.get("provider_id")
    if pid:
        owned_or_404(db, Provider, pid, user_id)


@router.get("")
def list_bots(
    include_deleted: bool = Query(default=False),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    """Bot 管理列表默认不返回软删除的 Bot。

    删除是软删除：数据行保留下来供历史消息引用，但不应继续出现在管理页。
    """
    q = select(Bot).where(Bot.user_id == user.id).order_by(Bot.created_at)
    if not include_deleted:
        q = q.where(Bot.deleted_at.is_(None))
    return [bot_out(b) for b in db.scalars(q).all()]


@router.get("/tools")
def list_tools(user: User = Depends(current_user)):
    return [
        {"name": t.name, "description": t.description, "level": t.level}
        for t in BUILTIN_TOOLS.values()
    ]


@router.get("/templates")
def list_templates(user: User = Depends(current_user)):
    """内置角色模板，新建 Bot 时可直接套用。"""
    return [template_out(t) for t in BOT_TEMPLATES]


@router.post("", status_code=201)
def create_bot(payload: BotIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    data = payload.model_dump()
    _validate(data, user.id, db)
    bot = Bot(user_id=user.id, **data)
    db.add(bot)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        raise HTTPException(400, f"已存在名为「{data.get('name')}」的 Bot")
    return bot_out(bot)


@router.get("/{bot_id}")
def get_bot(bot_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    return bot_out(owned_or_404(db, Bot, bot_id, user.id))


@router.patch("/{bot_id}")
def update_bot(
    bot_id: str,
    payload: BotPatch,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    bot = owned_or_404(db, Bot, bot_id, user.id)
    data = {k: v for k, v in payload.model_dump(exclude_unset=True).items()}
    _validate(data, user.id, db, exclude_id=bot.id)
    for k, v in data.items():
        setattr(bot, k, v)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        raise HTTPException(400, f"已存在名为「{data.get('name')}」的 Bot")
    return bot_out(bot)


@router.delete("/{bot_id}")
def delete_bot(bot_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    bot = owned_or_404(db, Bot, bot_id, user.id)
    direct = db.scalar(select(Run.id).where(Run.target_bot_id == bot.id, Run.status.in_(["queued", "running", "waiting"])))
    if direct:
        raise HTTPException(409, "该 Bot 正在直接处理消息，请先停止相关执行再删除")
    running = db.scalars(
        select(Task).join(Run, Run.id == Task.run_id).where(
            Task.assigned_bot_id == bot.id,
            Task.status.in_(["running", "waiting_approval", "waiting_reply"]),
            Run.status.in_(["queued", "running", "waiting"]),
        )
    ).all()
    if running:
        raise HTTPException(409, "该 Bot 正在执行任务，请先停止相关执行再删除")
    chats = db.scalars(select(Chat).where(Chat.supervisor_bot_id == bot.id)).all()
    if chats:
        raise HTTPException(409, "该 Bot 是某些群聊的总指挥，请先更换总指挥再删除")
    member_chats = list(
        db.scalars(select(ChatMember.chat_id).where(ChatMember.bot_id == bot.id)).all()
    )
    bot.deleted_at = now()
    for m in db.scalars(select(ChatMember).where(ChatMember.bot_id == bot.id)).all():
        db.delete(m)
    db.flush()

    # 让正打开的群聊实时把该成员从成员栏移除（历史消息仍保留）
    if member_chats:
        from .chats import _members, _members_event

        for cid in member_chats:
            chat = db.get(Chat, cid)
            if chat:
                _members_event(db, chat, _members(db, cid))
        db.flush()
    return {"ok": True}
