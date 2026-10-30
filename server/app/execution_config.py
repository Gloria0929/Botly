"""执行开始时冻结 Bot 配置；快照只用于后端，不由序列化接口返回。"""
from sqlalchemy.orm import Session

from .models import Bot, Provider, Run

BOT_FIELDS = ("id", "user_id", "name", "avatar", "role", "system_prompt", "provider_id",
              "model", "temperature", "tools", "is_supervisor_template")
PROVIDER_FIELDS = ("id", "user_id", "name", "kind", "base_url", "api_key_encrypted", "models")


def capture_bots(db: Session, bots: list[Bot]) -> dict:
    result = {}
    for bot in bots:
        item = {key: getattr(bot, key) for key in BOT_FIELDS}
        provider = db.get(Provider, bot.provider_id) if bot.provider_id else None
        item["provider"] = {key: getattr(provider, key) for key in PROVIDER_FIELDS} if provider else None
        result[bot.id] = item
    return result


def execution_bot(db: Session, run: Run, bot_id: str) -> Bot | None:
    item = (run.config_snapshot or {}).get("bot_configs", {}).get(bot_id)
    if item:
        return Bot(**{key: item[key] for key in BOT_FIELDS if key in item})
    return db.get(Bot, bot_id)  # 兼容旧执行，以及执行中后来加入的成员。


def execution_provider(db: Session, run: Run, bot: Bot) -> Provider | None:
    item = (run.config_snapshot or {}).get("bot_configs", {}).get(bot.id)
    if item is not None:
        provider = item.get("provider")
        return Provider(**provider) if provider else None
    return db.get(Provider, bot.provider_id) if bot.provider_id else None


def supervisor_id(run: Run, chat) -> str | None:
    return ((run.config_snapshot or {}).get("supervisor") or {}).get("id") or chat.supervisor_bot_id
