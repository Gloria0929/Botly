"""序列化工具：统一出参，绝不返回加密后的 Key 明文。"""
from __future__ import annotations

from datetime import datetime

from app.core.models import Bot, Chat, Message, Provider, Run, Task


def iso(dt: datetime | None) -> str | None:
    return dt.isoformat() if dt else None


def bot_out(bot: Bot, deleted_label: bool = False) -> dict:
    return {
        "id": bot.id,
        "name": bot.name,
        "avatar": bot.avatar,
        "role": bot.role,
        "system_prompt": bot.system_prompt,
        "persona": bot.persona,
        "proactivity": bot.proactivity,
        "provider_id": bot.provider_id,
        "model": bot.model,
        "temperature": bot.temperature,
        "tools": bot.tools or [],
        "skills": bot.skills or [],
        # 老库刚加列时可能还是 NULL，按「启用」处理
        "enabled": bot.enabled is not False,
        "deleted": bool(bot.deleted_at),
        "display_name": bot.name if not bot.deleted_at else f"{bot.name}（已删除）",
        "created_at": iso(bot.created_at),
    }


def _capability_summary(models: list | None) -> dict:
    """MOD-02：已探测能力汇总，供设置页显示「哪些模型不能用作总指挥」。"""
    total = known = tool_ok = tool_bad = stream_bad = 0
    for m in models or []:
        if not isinstance(m, dict):
            continue
        total += 1
        streaming = m.get("streaming")
        tool = m.get("tool_calling")
        if isinstance(streaming, bool) or isinstance(tool, bool):
            known += 1
        if tool is True:
            tool_ok += 1
        elif tool is False:
            tool_bad += 1
        if streaming is False:
            stream_bad += 1
    return {
        "total": total,
        "known": known,
        "tool_calling": tool_ok,
        "no_tool_calling": tool_bad,
        "no_streaming": stream_bad,
    }


def provider_out(p: Provider) -> dict:
    from app.services.vendors import BY_ID

    vendor = BY_ID.get(p.vendor_id or "")
    if p.kind == "mock":
        vendor_name, logo, color = "演示模型", "M", "#8B5CF6"
    elif vendor:
        vendor_name, logo, color = vendor.name, vendor.logo, vendor.color
    else:
        vendor_name, logo, color = "自定义", "＋", "#6B7280"
    return {
        "id": p.id,
        "name": p.name,
        "kind": p.kind,
        "vendor_id": p.vendor_id,
        "vendor_name": vendor_name,
        "logo": p.logo or logo,
        "color": color,
        "base_url": p.base_url,
        "api_key_last4": p.api_key_last4,
        "models": p.models or [],
        "status": p.status,
        "last_checked_at": iso(p.last_checked_at),
        "capabilities": _capability_summary(p.models),
    }



def chat_out(chat: Chat, supervisor: Bot | None, members: list[Bot]) -> dict:
    return {
        "id": chat.id,
        "name": chat.name,
        "supervisor_bot_id": chat.supervisor_bot_id,
        "supervisor": bot_out(supervisor) if supervisor else None,
        "members": [bot_out(m) for m in members],
        "run_mode": chat.run_mode,
        "mode": chat.mode,
        "max_speakers": chat.max_speakers,
        "max_rounds": chat.max_rounds,
        "muted": chat.muted,
        "limits_override": chat.limits_override or {},
        "always_allow_tools": chat.always_allow_tools or [],
        "require_approval_for_write": chat.require_approval_for_write,
        "last_active_at": iso(chat.last_active_at),
        "created_at": iso(chat.created_at),
    }


def message_out(m: Message, sender_bot: Bot | None = None) -> dict:
    data = {
        "id": m.id,
        "thread_id": m.thread_id,
        "run_id": m.run_id,
        "seq": m.seq,
        "sender_type": m.sender_type,
        "sender_bot_id": m.sender_bot_id,
        "type": m.type,
        "content": m.content,
        "meta": m.meta or {},
        "status": m.status,
        "created_at": iso(m.created_at),
    }
    # 已删除/已移出群聊的 Bot 不在成员列表里，历史消息仍要能显示是谁说的（BOT-03）
    if sender_bot is not None:
        data["sender_bot_name"] = (
            sender_bot.name
            if not sender_bot.deleted_at
            else f"{sender_bot.name}（已删除）"
        )
        data["sender_bot_avatar"] = sender_bot.avatar
        data["sender_bot_role"] = sender_bot.role
        data["sender_bot_deleted"] = bool(sender_bot.deleted_at)
    return data


def run_out(run: Run, extra_tokens: int = 0, extra_steps: int = 0) -> dict:
    # RUN-12：并行子执行单独记账，展示时把它们的消耗合并到父执行上，
    # 否则群聊底部的「tokens / 步数」会明显偏低。
    return {
        "id": run.id,
        "chat_id": run.chat_id,
        "status": run.status,
        "steps_used": run.steps_used + extra_steps,
        "tokens_used": run.tokens_used + extra_tokens,
        "started_at": iso(run.started_at),
        "ended_at": iso(run.ended_at),
        "end_reason": run.end_reason,
        "mode": run.mode,
        "kind": run.kind,
        "free_phase": (run.config_snapshot.get("free") or {}).get("phase") if run.kind == "free" else None,
        "round": run.round,
        "target_bot_id": run.target_bot_id,
        "resume": run.resume_payload,
        # RUN-12：并行子执行对前端透明，只有调度/排障时才需要区分
        "parent_run_id": run.parent_run_id,
        "parent_task_id": run.parent_task_id,
    }


def task_out(task: Task, bot: Bot | None) -> dict:
    return {
        "id": task.id,
        "run_id": task.run_id,
        "thread_id": task.thread_id,
        "assigned_bot_id": task.assigned_bot_id,
        "bot_name": bot.name if bot else "",
        "bot_avatar": bot.avatar if bot else "bot",
        "instruction": task.instruction,
        "expected_output": task.expected_output,
        "status": task.status,
        "result": task.result,
        "attempt": task.attempt,
        "step_key": task.step_key,
        "batch": task.batch or 0,
        "tokens_used": task.tokens_used,
        "started_at": iso(task.started_at),
        "ended_at": iso(task.ended_at),
    }
