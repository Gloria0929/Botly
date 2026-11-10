"""自由群聊路由：用一次很短的托管模型调用，按消息内容和成员特点挑出该接话的人。

路由只给建议。静音、被 @、发言人数与轮次上限、积极度为 0 的成员不参与等规则，
仍由调度器强制执行。调用超时、模型出错或返回无法解析时返回 None，调度器退回原有的轮换规则，
不会因为路由失败而没人回应。

模型调用复用 llm.stream_chat：同一套 OpenAI 兼容接入、SSRF 防护和密钥管理，不需要部署本地模型。
"""
from __future__ import annotations

import asyncio
import json
import re

from app.agent.llm import stream_chat
from app.core.logging_setup import get_logger

log = get_logger(__name__)

ROUTER_MAX_OUTPUT_TOKENS = 160
HISTORY_TEXT_LIMIT = 160
PERSONA_LIMIT = 60
REASON_LIMIT = 30

ROUTER_SYSTEM_PROMPT = """你是群聊的“接话调度员”，只做一件事：判断新消息该由哪几位成员来接话。

规则：
1. 按话题与成员的专长、性格、最近的发言情况来选，不要按名单顺序，也不要总选同一批人。
2. 最合适的人排第一，他是主要回应者。其余最多再选 {extra} 位，只选确实有不同视角或补充的人，不凑人数。
3. 闲聊、感受、玩笑、个人经验类话题，选性格合适的成员，不必限于专业对口。
4. 用户在追问或说“继续”时，把刚才在回答的那位排第一；话题已经换了就换人。
5. 积极度只是参考，越高越爱主动发言。最近发言次数多的成员，同等条件下让给别人。
6. 只能从成员列表里选，用方括号里的编号。
7. 只输出 JSON，不要解释：{{"speakers":[{{"id":"m1","reason":"不超过20字的理由"}}]}}"""


def _clip(text: str, limit: int) -> str:
    text = re.sub(r"\s+", " ", (text or "").strip())
    return text if len(text) <= limit else text[:limit] + "…"


def build_messages(*, content: str, history: list[dict], members: list[dict], max_pick: int) -> list[dict]:
    """members 每项含 id、name、role、persona、proactivity、recent；编号 m1、m2… 由这里生成。"""
    lines = ["成员："]
    for index, member in enumerate(members, 1):
        persona = _clip(member.get("persona") or "", PERSONA_LIMIT)
        lines.append(
            f"[m{index}] {member['name']}｜职位：{member.get('role') or '成员'}"
            + (f"｜性格：{persona}" if persona else "")
            + f"｜积极度：{member.get('proactivity', 0.5):g}｜最近发言：{member.get('recent', 0)} 次"
        )
    lines.append("")
    if history:
        lines.append("最近的群聊（旧到新）：")
        lines.extend(f"{item['who']}：{_clip(item['text'], HISTORY_TEXT_LIMIT)}" for item in history)
        lines.append("")
    lines.append(f"新消息（用户）：{_clip(content, 400)}")
    return [
        {"role": "system", "content": ROUTER_SYSTEM_PROMPT.format(extra=max(0, max_pick - 1))},
        {"role": "user", "content": "\n".join(lines)},
    ]


def parse_decision(text: str, members: list[dict], max_pick: int) -> list[dict]:
    """把模型输出解析成 [{"bot_id", "reason"}]。无法解析或没有有效成员时返回空列表。"""
    text = (text or "").strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.I)
    match = re.search(r"\{.*\}", text, flags=re.S)
    if not match:
        return []
    try:
        data = json.loads(match.group(0))
    except (ValueError, TypeError):
        return []
    items = data.get("speakers") if isinstance(data, dict) else None
    if not isinstance(items, list):
        return []
    by_token = {f"m{index}": member for index, member in enumerate(members, 1)}
    by_name = {member["name"]: member for member in members}
    picked, seen = [], set()
    for item in items:
        if isinstance(item, str):
            item = {"id": item}
        if not isinstance(item, dict):
            continue
        raw = str(item.get("id") or item.get("bot_id") or item.get("name") or "").strip().strip("[]")
        member = by_token.get(raw.lower()) or by_name.get(raw)
        if not member or member["id"] in seen:
            continue
        seen.add(member["id"])
        reason = _clip(str(item.get("reason") or ""), REASON_LIMIT)
        picked.append({"bot_id": member["id"], "reason": reason})
        if len(picked) >= max_pick:
            break
    return picked


async def _collect(stream) -> str:
    text = ""
    async for chunk in stream:
        if chunk.get("type") == "delta":
            text += chunk.get("text", "")
        elif chunk.get("type") == "done":
            return chunk.get("content") or text
    return text


async def route_members(
    *, kind: str, base_url: str, api_key: str | None, model: str, content: str, history: list[dict],
    members: list[dict], max_pick: int, timeout: float,
) -> list[dict] | None:
    """返回按优先级排列的 [{"bot_id", "reason"}]；None 表示路由不可用，调用方应走原有规则。"""
    if kind == "mock" or not members:
        return None
    messages = build_messages(content=content, history=history, members=members, max_pick=max_pick)
    try:
        text = await asyncio.wait_for(_collect(stream_chat(
            kind=kind, base_url=base_url, api_key=api_key, model=model, messages=messages,
            temperature=0, max_output_tokens=ROUTER_MAX_OUTPUT_TOKENS, max_retries=0,
            ctx={"role": "router", "bot_name": "路由", "observer": True},
        )), timeout)
    except asyncio.TimeoutError:
        log.warning("自由群聊路由超时（%.1fs），回退到轮换规则", timeout)
        return None
    except Exception as exc:  # 路由只是锦上添花，任何模型错误都不能影响回复。
        log.warning("自由群聊路由失败，回退到轮换规则：%s", type(exc).__name__)
        return None
    picked = parse_decision(text, members, max_pick)
    if not picked:
        log.warning("自由群聊路由返回无法解析，回退到轮换规则")
        return None
    return picked
