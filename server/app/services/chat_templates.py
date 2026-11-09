"""内置群聊模板：复用可用成员，补齐缺少的角色，不改写已有 Bot 配置。"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agent.capabilities import capability_of
from app.core.models import Bot, Provider
from app.services.templates import BY_ID


CHAT_TEMPLATES = [
    {"id": "market-research", "name": "市场调研", "avatar": "search",
     "description": "检索行业与竞品，分析数据，交付有来源的调研报告。",
     "members": ["supervisor", "researcher", "analyst", "writer", "reviewer"]},
    {"id": "content-writing", "name": "内容创作", "avatar": "pen",
     "description": "从选题和资料收集到成稿、审校，协作完成内容。",
     "members": ["supervisor", "researcher", "writer", "reviewer"]},
    {"id": "software-development", "name": "软件开发", "avatar": "wrench",
     "description": "拆解需求，编写可状态的代码，再检查质量和交付结果。",
     "members": ["supervisor", "coder", "reviewer"]},
    {"id": "data-analysis", "name": "数据分析", "avatar": "chart",
     "description": "清洗数据、计算指标、生成分析脚本，复核结论。",
     "members": ["supervisor", "analyst", "coder", "reviewer"]},
    {"id": "product-planning", "name": "产品策划", "avatar": "compass",
     "description": "梳理用户需求，形成产品方案，并验证实现思路。",
     "members": ["supervisor", "researcher", "writer", "coder", "reviewer"]},
]
TEMPLATE_BY_ID = {t["id"]: t for t in CHAT_TEMPLATES}
ROLE_ALIASES = {
    "supervisor": {"总指挥"}, "researcher": {"研究员"},
    "writer": {"架构师", "写作员", "编辑"}, "coder": {"程序员"},
    "analyst": {"数据分析师"}, "reviewer": {"终审", "评审员"},
}


def member_spec(template_id: str) -> dict:
    spec = dict(BY_ID[template_id])
    if template_id == "writer":
        spec.update(name="写作员", role="写作员")
        spec["system_prompt"] = spec["system_prompt"].replace("架构师", "写作员")
    if template_id in ("coder", "analyst"):
        spec["tools"] = [*spec["tools"], "code_exec", "run_command"]
    return spec


def template_out(template: dict) -> dict:
    return {**template, "members": [
        {"name": spec["name"], "role": spec["role"], "avatar": spec["avatar"]}
        for spec in (member_spec(key) for key in template["members"])
    ]}


def assemble_members(db: Session, user_id: str, template: dict) -> tuple[list[Bot], list[str], bool]:
    all_bots = list(db.scalars(select(Bot).where(
        Bot.user_id == user_id, Bot.deleted_at.is_(None),
    ).order_by(Bot.created_at)))
    enabled = [b for b in all_bots if b.enabled is not False]
    providers = {p.id: p for p in db.scalars(select(Provider).where(
        Provider.user_id == user_id,
    ).order_by(Provider.created_at))}

    def model_for(bot: Bot) -> tuple[str, str] | None:
        provider = providers.get(bot.provider_id or "")
        if not provider:
            return None
        model = bot.model or next((m.get("name") for m in provider.models or [] if m.get("name")), None)
        if not model:
            return None
        caps = capability_of(provider, model)
        if caps["streaming"] is False or caps["tool_calling"] is False:
            return None
        return provider.id, model

    # 优先沿用现有总指挥的配置；没有可用成员时，再从已接入的厂商里选择。
    candidates = sorted(enabled, key=lambda b: b.role != "总指挥" and not b.is_supervisor_template)
    default_model = next((config for b in candidates if (config := model_for(b))), None)
    if not default_model:
        for provider in providers.values():
            model = next((m.get("name") for m in provider.models or []
                          if m.get("name") and m.get("streaming") is not False and m.get("tool_calling") is not False), None)
            if model:
                default_model = provider.id, model
                break

    members = []
    created = []
    names = {b.name for b in all_bots}
    for key in template["members"]:
        spec = member_spec(key)
        aliases = ROLE_ALIASES[key] | {BY_ID[key]["name"], BY_ID[key]["role"]}
        matches = [b for b in enabled if b not in members and (
            b.role in aliases or b.name in aliases
            or (key == "supervisor" and b.is_supervisor_template)
        )]
        matches.sort(key=lambda b: (model_for(b) is None, b.name != spec["name"]))
        bot = next((b for b in matches if model_for(b)), None)
        if bot is None and not default_model:
            # 尚未接入模型时也可以先组建团队；已配置但能力不兼容的成员不复用。
            bot = next((b for b in matches if not b.provider_id), None)
        if bot is None:
            name = spec["name"]
            suffix = 2
            while name in names:
                name = f"{spec['name']} {suffix}"
                suffix += 1
            bot = Bot(
                user_id=user_id, name=name, role=spec["role"], avatar=spec["avatar"],
                system_prompt=spec["system_prompt"], persona=spec.get("persona", ""), tools=list(spec["tools"]),
                provider_id=default_model[0] if default_model else None,
                model=default_model[1] if default_model else None,
                is_supervisor_template=key == "supervisor",
            )
            db.add(bot)
            db.flush()
            names.add(name)
            created.append(bot.id)
        members.append(bot)
    return members, created, default_model is None
