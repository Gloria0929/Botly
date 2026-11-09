"""角色人设的单一来源、模式分离、旧模板识别及配置快照。"""
import hashlib
from types import SimpleNamespace

from sqlalchemy import select

from app.agent.execution_config import capture_bots, execution_bot
from app.agent.orchestrator import RunExecutor
from app.core.db import SessionLocal
from app.core.models import Bot, Chat, Run, Thread
from app.services import templates
from tests.conftest import register, setup_workspace


LEGACY_RESEARCHER = (
    "你是研究员，擅长检索与核实信息。收到任务后先用 web_search / web_read 收集资料，"
    "把关键事实、数据与来源整理成结构化要点，必要时写入文件。不要编造数据，"
    "找不到就明确说明找不到。"
)


def prompt(client, chat_id, bot_id, mode):
    with SessionLocal() as db:
        chat = db.get(Chat, chat_id)
        thread = db.scalar(select(Thread).where(Thread.chat_id == chat_id, Thread.type == "main"))
        run = Run(user_id=chat.user_id, chat_id=chat_id, kind=mode, config_snapshot={})
        db.add(run); db.flush()
        bot = db.get(Bot, bot_id)
        members = list(db.scalars(select(Bot).where(Bot.user_id == chat.user_id)))
        text = RunExecutor(run.id)._build_system_prompt(db, run, chat, bot, bot_id == chat.supervisor_bot_id, members, thread)
        db.rollback()
        return text


def test_templates_keep_persona_and_duty_separate():
    for template in templates.BOT_TEMPLATES:
        assert template["persona"] and "【语气参考】" in template["persona"]
        assert "【语气参考】" not in template["system_prompt"]
        assert "[PASS]" not in template["system_prompt"]
        assert "用 @名字" not in template["system_prompt"]
    assert "收到任务后先用 web_search" not in templates.BY_ID["researcher"]["system_prompt"]
    assert "零外部依赖" not in templates.BY_ID["coder"]["system_prompt"]


def test_both_modes_use_the_saved_persona_without_forced_task_rituals(client, invite):
    register(client, invite, "role-modes@test.dev")
    cid, bots = setup_workspace(client)
    researcher = next(bot for bot in bots if bot["name"] == "研究员")
    assert researcher["persona"] == templates.BY_ID["researcher"]["persona"]
    free = prompt(client, cid, researcher["id"], "free")
    task = prompt(client, cid, researcher["id"], "task")
    assert researcher["persona"] in free and researcher["persona"] in task
    assert "收到任务后先用 web_search" not in free + task
    assert "正文中的 @ 不会启动任务" in task
    assert "[PASS]" not in task and "[PASS]" in free
    client.patch(f"/api/bots/{researcher['id']}", json={"persona": "用简短粤语交流，不讲冷笑话"})
    for mode in ("free", "task"):
        assert "用简短粤语交流，不讲冷笑话" in prompt(client, cid, researcher["id"], mode)


def test_legacy_template_is_normalized_but_not_written_back_or_guessed_by_name(client, invite):
    register(client, invite, "role-legacy@test.dev")
    cid, bots = setup_workspace(client)
    bot = next(bot for bot in bots if bot["name"] == "研究员")
    client.patch(f"/api/bots/{bot['id']}", json={"system_prompt": LEGACY_RESEARCHER, "persona": ""})
    saved = client.get(f"/api/bots/{bot['id']}").json()
    assert saved["template_id"] == "researcher" and saved["template_update_available"] is True
    for mode in ("free", "task"):
        assert "收到任务后先用 web_search" not in prompt(client, cid, bot["id"], mode)
    assert client.get(f"/api/bots/{bot['id']}").json()["system_prompt"] == LEGACY_RESEARCHER

    custom = LEGACY_RESEARCHER + "\n只讨论海洋生态，用生活中的类比解释。"
    client.patch(f"/api/bots/{bot['id']}", json={"system_prompt": custom})
    saved = client.get(f"/api/bots/{bot['id']}").json()
    assert saved["template_id"] is None and saved["template_update_available"] is False
    for mode in ("free", "task"):
        assert custom in prompt(client, cid, bot["id"], mode)


def test_known_embedded_persona_and_voice_are_not_lost(monkeypatch):
    old = "你是研究员。好奇、直率，只问真正不确定的事。\n\n【职责】先查资料。\n\n【语气参考】先核对来源。（只是参考风格，不要照搬原句。）\n\n【群聊规则】旧规则。"
    monkeypatch.setitem(templates.LEGACY_PROMPTS, hashlib.sha256(old.encode()).hexdigest(), "researcher")
    bot = SimpleNamespace(system_prompt=old, persona="")
    profile = templates.role_profile(bot)
    assert "好奇、直率" in profile["persona"] and "先核对来源" in profile["persona"]
    assert "你是研究员" not in profile["persona"] and "旧规则" not in profile["prompt"]


def test_saving_template_update_keeps_identity_tools_and_existing_run_snapshot(client, invite):
    register(client, invite, "role-snapshot@test.dev")
    cid, bots = setup_workspace(client)
    bot = next(bot for bot in bots if bot["name"] == "研究员")
    client.patch(f"/api/bots/{bot['id']}", json={"system_prompt": LEGACY_RESEARCHER, "persona": "先给结论，不用表格"})
    bot = client.get(f"/api/bots/{bot['id']}").json()
    with SessionLocal() as db:
        original = db.get(Bot, bot["id"])
        frozen = capture_bots(db, [original])
        run = Run(user_id=original.user_id, chat_id=cid, config_snapshot={"bot_configs": frozen})
        db.add(run); db.commit()
        run_id = run.id
    template = next(item for item in client.get("/api/bots/templates").json() if item["id"] == "researcher")
    updated = client.patch(f"/api/bots/{bot['id']}", json={"system_prompt": template["system_prompt"]}).json()
    assert updated["template_update_available"] is False
    assert updated["persona"] == "先给结论，不用表格"
    for key in ("name", "role", "provider_id", "model", "tools", "skills", "avatar", "proactivity"):
        assert updated[key] == bot[key]
    with SessionLocal() as db:
        previous = execution_bot(db, db.get(Run, run_id), bot["id"])
        assert previous.system_prompt == LEGACY_RESEARCHER and previous.persona == "先给结论，不用表格"
