"""长期记忆只依据群聊中的用户原话，避免把 Bot 的复述和任务输出当作偏好。"""
from __future__ import annotations

import re

from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session

from app.core.models import Message, Thread
from app.core.similarity import is_duplicate, normalize, similarity

MIN_MEMORY_MENTIONS = 3
MIN_EVIDENCE_SIMILARITY = 0.085
MAX_MEMORY_EVIDENCE = 10
MEMORY_SOURCE_LIMIT = 100
_EXPLICIT_MEMORY_REQUEST = re.compile(r"(?:请|帮我|麻烦)?(?:记住|记下|记牢)")


def user_source_messages(db: Session, chat_id: str, *, trigger_message_id: str | None = None,
                         run_id: str | None = None, bot_id: str | None = None) -> list[dict[str, str]]:
    """已完成的主/分支用户消息；模型生成的子任务指令不计为用户表达。"""
    through_seq = None
    if trigger_message_id:
        trigger = db.get(Message, trigger_message_id)
        through_seq = trigger.seq if trigger else None
    if run_id:
        run_seq = db.scalar(select(Message.seq).where(
            Message.run_id == run_id, Message.sender_type == "user", Message.type == "text",
            Message.status == "done",
        ).order_by(Message.seq.desc()).limit(1))
        if run_seq is not None:
            through_seq = max(through_seq or 0, run_seq)
    visible_threads = Thread.type == "main"
    if bot_id:
        visible_threads = or_(visible_threads, and_(Thread.type == "branch", Thread.bot_id == bot_id))
    rows = db.execute(
        select(Message.id, Message.content, Message.meta)
        .join(Thread, Thread.id == Message.thread_id)
        .where(
            Thread.chat_id == chat_id,
            visible_threads,
            Message.sender_type == "user",
            Message.type == "text",
            Message.status == "done",
            *((Message.seq <= through_seq,) if through_seq is not None else ()),
        )
        .order_by(Message.seq.desc())
        .limit(MEMORY_SOURCE_LIMIT)
    ).all()
    return [
        {"id": message_id, "content": content.strip()}
        for message_id, content, meta in reversed(rows)
        if content and content.strip() and not (meta or {}).get("delegation")
    ]


def verified_evidence(candidate: dict, sources: dict[str, str]) -> list[dict[str, str]]:
    """只接纳能逐字定位到不同用户消息的原话引用。"""
    verified = []
    seen = set()
    for item in candidate.get("evidence", []):
        if not isinstance(item, dict):
            continue
        source_id = str(item.get("message_id") or "")
        quote = str(item.get("quote") or "").strip()
        if source_id in seen or source_id not in sources or len(normalize(quote)) < 8:
            continue
        if (quote not in sources[source_id]
                or similarity(candidate["content"], quote) < MIN_EVIDENCE_SIMILARITY):
            continue
        seen.add(source_id)
        verified.append({"message_id": source_id, "quote": quote[:180]})
    return verified


def explicitly_requested_memory(source: str, content: str) -> bool:
    """明确让 Bot 记住并且和待存内容相符时，允许本次直接存入私有记忆。"""
    if not source or not content or not _EXPLICIT_MEMORY_REQUEST.search(source):
        return False
    return is_duplicate(source, content, threshold=0.30)


def repeated_memory_sources(content: str, sources: list[dict[str, str]]) -> list[str]:
    """返回至少支持同一条记忆的不同原话消息；一次消息只能贡献一次。"""
    if not content:
        return []
    return [
        source["id"] for source in sources
        if is_duplicate(content, source["content"], threshold=0.095)
    ]
