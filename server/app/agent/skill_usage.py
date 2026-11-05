"""按执行、线程和成员记录技能选择；只有成功返回的正文才算已加载。"""
from __future__ import annotations

from dataclasses import dataclass, field
import json

from sqlalchemy import select

from app.core.models import Message


SKILL_TOOLS = {"skill_select", "skill_file_read"}


@dataclass
class SkillUsage:
    loaded_ids: set[str] = field(default_factory=set)


def call_source(db, run_id: str, thread_id: str, bot_id: str, call_id: str | None):
    if not call_id:
        return None
    rows = db.scalars(select(Message).where(
        Message.run_id == run_id, Message.thread_id == thread_id,
        Message.sender_bot_id == bot_id, Message.type == "tool_call",
    ).order_by(Message.seq.desc()))
    return next((row for row in rows if any(call.get("id") == call_id
                for call in (row.meta or {}).get("tool_calls", []))), None)


def usage_state(db, run_id: str, thread_id: str, bot_id: str, skills) -> SkillUsage:
    from app.tools.skills import sandbox_path

    allowed = {skill.id for skill in skills}
    query = select(Message).where(
        Message.run_id == run_id, Message.thread_id == thread_id,
        Message.type == "tool_result", Message.status == "done",
        Message.meta["tool"].as_string().in_(SKILL_TOOLS),
    )
    state = SkillUsage()
    for row in db.scalars(query.order_by(Message.seq)):
        meta = row.meta or {}
        usage = meta.get("skill_usage")
        if not usage:
            # 兼容已经在执行中的旧 skill_file_read 记录；失败和参考文件不算选用。
            if meta.get("tool") != "skill_file_read":
                continue
            source = call_source(db, run_id, thread_id, bot_id, meta.get("tool_call_id"))
            if not source:
                continue
            call = next(c for c in source.meta["tool_calls"] if c.get("id") == meta.get("tool_call_id"))
            try:
                args = json.loads((call.get("function") or {}).get("arguments") or "{}")
            except (ValueError, TypeError):
                continue
            if not isinstance(args, dict):
                continue
            skill = next((s for s in skills if args.get("path") in (
                f"{s.name}/SKILL.md", f"{s.id}/SKILL.md", f"{sandbox_path(s)}/SKILL.md",
            ) and row.content.startswith(f"技能「{s.name}」的完整说明")), None)
            if not skill:
                continue
            usage = {"bot_id": bot_id, "loaded": [skill.id],
                     "interjection_seq": source.meta.get("interjection_seq", 0)}
        if usage.get("bot_id") != bot_id:
            continue
        state.loaded_ids.update(set(usage.get("loaded", [])) & allowed)
    return state


def usage_prompt(skills, state: SkillUsage) -> str:
    loaded = "、".join(s.name for s in skills if s.id in state.loaded_ids) or "无"
    lines = ["## 当前任务的技能选择", f"本成员在本线程已加载：{loaded}。"]
    lines.append("根据当前任务的目标、复杂度和现有上下文，自主判断技能是否能提供有用的方法或资源。"
                 "需要时用 skill_select 按名称加载相关技能，或 skill_file_read 读取具体说明；已加载的正文可复用。"
                 "不需要技能时直接回答或调用业务工具，无需选择空列表、说明跳过理由或等待确认。"
                 "任务阶段或用户要求变化后再按需判断，技能数量不是完成质量的依据。")
    return "\n".join(lines)
