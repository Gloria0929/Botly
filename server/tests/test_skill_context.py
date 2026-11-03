"""技能目录不能因正文过长丢失，延迟加载仍受 Bot 的技能授权约束。"""
from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest

from app.core.db import SessionLocal
from app.core.models import Bot, Skill
from app.tools import registry, skills as skillmod
from app.worker import process_one
from tests.conftest import register, setup_workspace


@pytest.mark.parametrize("long_index", [0, 1, 6])
def test_skill_catalog_is_complete_without_loading_bodies(long_index):
    skills = [
        Skill(name=f"技能{i}", description=f"用途{i}", prompt=f"步骤{i}", files=[])
        for i in range(7)
    ]
    original = skillmod.prompt_block(skills)
    skills[long_index].prompt = "长说明" * 3000
    skills[-1].prompt = "" if long_index != 6 else skills[-1].prompt
    block = skillmod.prompt_block(skills)

    assert "当前共启用 7 个技能" in block
    for skill in skills:
        assert f"### {skill.name}\n{skill.description}" in block
        assert f"{skill.name}/SKILL.md" in block
        if skill.prompt:
            assert skill.prompt not in block, "短技能与长技能都只能按需加载"
    assert block == original, "正文长度不能改变技能目录或初始上下文"
    assert "skill_file_read" in skillmod.effective_tool_names(skills)
    assert skillmod.prompt_block([]) == ""
    assert "skill_file_read" not in skillmod.effective_tool_names([])


@pytest.mark.parametrize("scenario,selected,task_request", [
    ("simple", [], "你有几个技能？"),
    ("specialist", ["skill-creator"], "为我们的发布流程创建一个可复用技能。"),
    ("complex", ["coding-agent", "ui-ux-pro-max", "reviewer-agent"],
     "实现一个管理界面：先分析代码，再设计界面，最后评审。"),
])
def test_agent_can_skip_or_load_selected_skills_by_stage(client, invite, monkeypatch, scenario, selected, task_request):
    from app.agent import orchestrator

    register(client, invite, f"skill-catalog-{scenario}@test.dev")
    chat_id, bots = setup_workspace(client)
    supervisor_id = client.get(f"/api/chats/{chat_id}").json()["supervisor_bot_id"]
    sup = next(b for b in bots if b["id"] == supervisor_id)
    names = [
        "find-skills", "coding-agent", "ui-ux-pro-max", "writing-style-skill",
        "reviewer-agent", "skill-vetter", "skill-creator",
    ]
    lengths = [2952, 2740, 14801, 2910, 1200, 3676, 22000]
    created = []
    for name, length in zip(names, lengths):
        response = client.post(
            "/api/skills",
            json={"name": name, "description": "用途说明" * 40,
                  "prompt": f"{name} 开始\n" + "说明" * (length // 2) + f"\n{name} 结束"},
        )
        assert response.status_code == 201, response.text
        created.append(response.json())
    assert client.patch(
        f"/api/bots/{sup['id']}", json={"skills": [s["id"] for s in created], "tools": []}
    ).status_code == 200
    turn = {"n": 0}

    async def fake_stream_chat(**kw):
        turn["n"] += 1
        system = kw["messages"][0]["content"]
        assert "当前共启用 7 个技能" in system
        for name in names:
            assert f"### {name}\n" in system
        assert "skill_file_read" in {t["function"]["name"] for t in kw["tools"]}
        for skill in created:
            assert skill["prompt"] not in system
        results = [m["content"] for m in kw["messages"] if m["role"] == "tool"]
        loaded = selected[:turn["n"] - 1]
        for name in loaded:
            skill = next(s for s in created if s["name"] == name)
            assert any(skill["prompt"] in result for result in results), "所选技能正文必须完整可读"
        for skill in created:
            if skill["name"] not in loaded:
                assert all(skill["prompt"] not in result for result in results), "无关技能不得自动加载"
        assert all(registry.UNTRUSTED_PREFIX not in result for result in results)
        if turn["n"] <= len(selected):
            name = selected[turn["n"] - 1]
            yield {"type": "done", "content": "", "tool_calls": [{
                "id": f"read-{name}", "type": "function",
                "function": {"name": "skill_file_read", "arguments": json.dumps(
                    {"path": f"{name}/SKILL.md"}
                )},
            }]}
            return
        yield {"type": "done", "content": "当前启用了 7 个技能。", "tool_calls": []}

    monkeypatch.setattr(orchestrator, "stream_chat", fake_stream_chat)
    run_id = client.post(
        f"/api/chats/{chat_id}/messages", json={"content": task_request}
    ).json()["run"]["id"]
    asyncio.run(process_one(run_id))
    run = client.get(f"/api/runs/{run_id}").json()
    assert run["status"] == "completed", run.get("end_reason")
    assert turn["n"] == len(selected) + 1


@pytest.mark.parametrize("unavailable", ["unbound", "disabled", "foreign"])
def test_skill_prompt_read_requires_enabled_bound_owned_skill(client, invite, unavailable):
    register(client, invite, f"skill-auth-{unavailable}@test.dev")
    bots = client.get("/api/bots").json()
    bot_id = bots[0]["id"]
    allowed = client.post("/api/skills", json={"name": "已授权", "prompt": "授权步骤"}).json()
    blocked = client.post("/api/skills", json={
        "name": "未授权", "prompt": "不应泄露的说明", "enabled": unavailable != "disabled",
    }).json()
    bound_ids = [allowed["id"]]
    if unavailable == "disabled":
        bound_ids.append(blocked["id"])
    client.patch(f"/api/bots/{bot_id}", json={"skills": bound_ids})

    with SessionLocal() as db:
        bot = db.get(Bot, bot_id)
        if unavailable == "foreign":
            register(client, invite, "skill-auth-other@test.dev")
            other = client.post("/api/skills", json={"name": "其他账号", "prompt": "不应泄露的说明"}).json()
            # 即使配置里混入其他账号的技能 id，也必须由 for_bot 过滤掉。
            bot.skills = [*bot.skills, other["id"]]
            db.flush()
            blocked = other
        ctx = SimpleNamespace(db=db, user_id=bot.user_id, bot=bot)
        for prefix in (blocked["name"], blocked["id"]):
            result = asyncio.run(registry._skill_file_read(ctx, f"{prefix}/SKILL.md"))
            assert "不应泄露的说明" not in result
            assert "没有这个文件" in result
        result = asyncio.run(registry._skill_file_read(ctx, f"{allowed['id']}/SKILL.md"))
        assert "授权步骤" in result


def test_skill_references_remain_untrusted(client, invite):
    register(client, invite, "skill-reference-boundary@test.dev")
    bot_id = client.get("/api/bots").json()[0]["id"]
    skill = client.post("/api/skills", json={"name": "参考技能", "prompt": "配置步骤"}).json()
    client.patch(f"/api/bots/{bot_id}", json={"skills": [skill["id"]]})
    with SessionLocal() as db:
        bot = db.get(Bot, bot_id)
        row = db.get(Skill, skill["id"])
        row.files = skillmod.save_package(bot.user_id, row.id, {"notes.md": "外部资料".encode()})
        db.flush()
        ctx = SimpleNamespace(db=db, user_id=bot.user_id, bot=bot)
        result = asyncio.run(registry._skill_file_read(ctx, "参考技能/notes.md"))
        assert result.startswith(registry.UNTRUSTED_PREFIX)
        assert "外部资料" in result
        catalog = skillmod.prompt_block([row])
        assert "参考技能/notes.md" not in catalog
        result = asyncio.run(registry._skill_file_read(ctx, "参考技能/SKILL.md"))
        assert "参考技能/notes.md" in result, "参考文件清单也在选用技能后才加载"
