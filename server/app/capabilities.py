"""模型能力（MOD-02）：读写 Provider.models 里的探测结果，并给出拦截理由。

`models` 是 JSON 列表，每项形如：

    {"name": "gpt-4o", "input_price": 2.5, "output_price": 10.0,
     "streaming": true, "tool_calling": true, "checked_at": "..."}

能力是三态：True（探测到支持）、False（探测到不支持）、None/缺失（没探测）。
只有明确探测到「不支持」才拦截；没探测过一律放行，避免把自建网关或一次
临时网络故障当成能力缺失。
"""
from __future__ import annotations

from .models import Bot, Provider

CAPABILITY_KEYS = ("streaming", "tool_calling", "checked_at")


def model_entry(provider: Provider | None, model: str | None) -> dict | None:
    """返回某个模型在 Provider.models 里的那一项。"""
    if provider is None or not model:
        return None
    for row in provider.models or []:
        if isinstance(row, dict) and row.get("name") == model:
            return row
    return None


def capability_of(provider: Provider | None, model: str | None) -> dict:
    """某模型的已知能力；未知项为 None。"""
    row = model_entry(provider, model) or {}
    out: dict = {}
    for key in ("streaming", "tool_calling"):
        value = row.get(key)
        out[key] = value if isinstance(value, bool) else None
    out["checked_at"] = row.get("checked_at") if row else None
    return out


def apply_capability(models: list[dict], name: str, caps: dict) -> list[dict]:
    """把探测结果写回 models；返回新列表（JSON 字段要整体赋值才会脏检查）。"""
    out = []
    for row in models:
        row = dict(row)
        if row.get("name") == name:
            for key in ("streaming", "tool_calling"):
                value = caps.get(key)
                if isinstance(value, bool):
                    row[key] = value
                else:
                    row.pop(key, None)
            row["checked_at"] = caps.get("checked_at")
        out.append(row)
    return out


def supervisor_block_reason(bot: Bot | None, provider: Provider | None) -> str | None:
    """该 Bot 不能担任总指挥的原因；可以担任时返回 None。

    总指挥要靠工具调用来拆任务、派活，模型明确不支持工具调用时，执行只会在
    第一步就失败，所以在选总指挥的位置就拦住（群聊创建 / 更换总指挥）。
    只拦「明确探测到不支持」的情况：没绑定模型、或还没探测过的，交给执行时
    报错，不在这里阻断用户搭群聊。
    """
    if bot is None or not bot.provider_id or not bot.model:
        return None
    caps = capability_of(provider, bot.model)
    if caps["tool_calling"] is False:
        return (
            f"「{bot.name}」使用的模型 {bot.model} 不支持工具调用，无法拆解与派活。"
            "请先换成支持工具调用的模型，或在设置页重新检测能力。"
        )
    if caps["streaming"] is False:
        return (
            f"「{bot.name}」使用的模型 {bot.model} 不支持流式输出，执行无法进行。"
            "请先换成支持流式输出的模型。"
        )
    return None
