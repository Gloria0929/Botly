"""记忆：共享记忆（用户级）与 Bot 私有记忆。"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import MEMORY_INJECT_LIMIT
from ..db import get_db
from ..deps import current_user, owned_or_404
from ..models import Bot, Memory, MemoryCandidate, User, now
from ..memory_context import memory_context

router = APIRouter(prefix="/memories", tags=["memories"])


class MemoryIn(BaseModel):
    scope: str = "shared"  # shared | bot
    bot_id: str | None = None
    title: str = ""
    content: str


class MemoryPatch(BaseModel):
    title: str | None = None
    content: str | None = None


def _out(m: Memory) -> dict:
    return {
        "id": m.id,
        "scope": m.scope,
        "bot_id": m.bot_id,
        "title": m.title,
        "content": m.content,
        "updated_at": m.updated_at.isoformat() if m.updated_at else None,
    }


def _candidate_out(c: MemoryCandidate) -> dict:
    return {
        "id": c.id,
        "run_id": c.run_id,
        "chat_id": c.chat_id,
        "scope": c.scope,
        "bot_id": c.bot_id,
        "title": c.title,
        "content": c.content,
        "status": c.status,
        "created_at": c.created_at.isoformat() if c.created_at else None,
    }


@router.get("/candidates")
def list_candidates(
    status: str = Query(default="pending"),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    """MEM-04：执行结束后生成的记忆候选，默认只看待确认的。"""
    q = select(MemoryCandidate).where(MemoryCandidate.user_id == user.id)
    if status:
        q = q.where(MemoryCandidate.status == status)
    rows = db.scalars(q.order_by(MemoryCandidate.created_at.desc()).limit(50)).all()
    return {"candidates": [_candidate_out(c) for c in rows]}


@router.post("/candidates/{candidate_id}/accept", status_code=201)
def accept_candidate(
    candidate_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)
):
    c = owned_or_404(db, MemoryCandidate, candidate_id, user.id)
    if c.status != "pending":
        raise HTTPException(400, "该候选已处理")
    m = Memory(
        user_id=user.id,
        scope=c.scope,
        bot_id=c.bot_id,
        title=c.title,
        content=c.content,
    )
    db.add(m)
    c.status = "accepted"
    c.resolved_at = now()
    db.flush()
    return _out(m)


@router.post("/candidates/{candidate_id}/reject")
def reject_candidate(
    candidate_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)
):
    c = owned_or_404(db, MemoryCandidate, candidate_id, user.id)
    if c.status == "pending":
        c.status = "rejected"
        c.resolved_at = now()
        db.flush()
    return {"ok": True}


@router.get("")
def list_memories(
    scope: str | None = None,
    bot_id: str | None = None,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    q = select(Memory).where(Memory.user_id == user.id)
    if scope:
        q = q.where(Memory.scope == scope)
    if bot_id:
        q = q.where(Memory.bot_id == bot_id)
    rows = db.scalars(q.order_by(Memory.updated_at.desc())).all()
    context_rows = rows
    if scope == "bot" and bot_id:
        owned_or_404(db, Bot, bot_id, user.id)
        context_rows = [*rows, *db.scalars(select(Memory).where(Memory.user_id == user.id, Memory.scope == "shared")).all()]
    context = memory_context(context_rows)
    return {
        "memories": [_out(m) for m in rows],
        "injected_length": context["injected_length"],
        "total_length": context["total_length"],
        "truncated": context["truncated"],
        "limit": MEMORY_INJECT_LIMIT,
    }


@router.post("", status_code=201)
def create_memory(
    payload: MemoryIn, user: User = Depends(current_user), db: Session = Depends(get_db)
):
    if payload.scope not in ("shared", "bot"):
        raise HTTPException(400, "scope 只能是 shared 或 bot")
    if not payload.content.strip():
        raise HTTPException(400, "内容不能为空")
    if payload.scope == "bot":
        if not payload.bot_id:
            raise HTTPException(400, "Bot 私有记忆必须指定 bot_id")
        owned_or_404(db, Bot, payload.bot_id, user.id)
    m = Memory(
        user_id=user.id,
        scope=payload.scope,
        bot_id=payload.bot_id if payload.scope == "bot" else None,
        title=payload.title.strip(),
        content=payload.content.strip(),
    )
    db.add(m)
    db.flush()
    return _out(m)


@router.patch("/{memory_id}")
def update_memory(
    memory_id: str,
    payload: MemoryPatch,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    m = owned_or_404(db, Memory, memory_id, user.id)
    if payload.title is not None:
        m.title = payload.title
    if payload.content is not None:
        m.content = payload.content
    db.flush()
    return _out(m)


@router.delete("/{memory_id}")
def delete_memory(
    memory_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)
):
    m = owned_or_404(db, Memory, memory_id, user.id)
    db.delete(m)
    db.flush()
    return {"ok": True}


@router.get("/stats")
def memory_stats(user: User = Depends(current_user), db: Session = Depends(get_db)):
    """按 Bot 统计注入长度，便于界面提示是否超限。"""
    bots = db.scalars(
        select(Bot).where(Bot.user_id == user.id, Bot.deleted_at.is_(None))
    ).all()
    all_memories = db.scalars(select(Memory).where(Memory.user_id == user.id)).all()
    shared = memory_context([m for m in all_memories if m.scope == "shared"])["total_length"]
    out = []
    for b in bots:
        candidates = [m for m in all_memories if m.scope == "shared" or m.bot_id == b.id]
        context = memory_context(candidates)
        private = context["total_length"] - shared
        out.append(
            {
                "bot_id": b.id,
                "bot_name": b.name,
                "shared_length": shared or 0,
                "private_length": private or 0,
                "total": context["total_length"],
                "injected_length": context["injected_length"],
                "over_limit": context["truncated"],
            }
        )
    return {"limit": MEMORY_INJECT_LIMIT, "bots": out}
