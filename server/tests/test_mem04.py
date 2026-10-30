"""MEM-04：执行结束后生成记忆候选，用户确认后才写入。"""
from __future__ import annotations

import asyncio

from app.db import SessionLocal
from app.models import Run
from app.orchestrator import RunExecutor, _parse_memory_candidates
from tests.conftest import register, setup_workspace


def test_parse_memory_candidates():
    assert _parse_memory_candidates(
        '[{"title":"偏好","content":"喜欢简洁中文"}]'
    ) == [{"title": "偏好", "content": "喜欢简洁中文"}]
    assert _parse_memory_candidates(
        '```json\n[{"content":"不用感叹号"}]\n```'
    ) == [{"title": "", "content": "不用感叹号"}]
    assert _parse_memory_candidates("没有值得记住的") == []
    assert _parse_memory_candidates("[]") == []
    # 最多 3 条
    raw = "[" + ",".join(f'{{"content":"第{i}条"}}' for i in range(6)) + "]"
    assert len(_parse_memory_candidates(raw)) == 3


def test_memory_candidate_generation_and_accept(client, invite, monkeypatch):
    register(client, invite, "mem04@test.dev")
    chat_id, _ = setup_workspace(client)
    r = client.post(f"/api/chats/{chat_id}/messages", json={"content": "以后回答尽量简洁"})
    run_id = r.json()["run"]["id"]

    async def fake_complete(self, **kwargs):
        return '[{"title":"表达偏好","content":"用户偏好简洁的中文表达"}]'

    monkeypatch.setattr(RunExecutor, "_complete_text", fake_complete)
    with SessionLocal() as db:
        db.get(Run, run_id).status = "completed"
        db.commit()

    asyncio.run(RunExecutor(run_id)._generate_memory_candidates("已完成"))

    body = client.get("/api/memories/candidates").json()
    assert len(body["candidates"]) == 1, body
    cand = body["candidates"][0]
    assert "简洁" in cand["content"]
    assert cand["status"] == "pending"

    accepted = client.post(f"/api/memories/candidates/{cand['id']}/accept")
    assert accepted.status_code == 201, accepted.text
    assert accepted.json()["content"] == cand["content"]

    # 已处理的候选不再出现在待确认列表
    assert client.get("/api/memories/candidates").json()["candidates"] == []
    mems = client.get("/api/memories?scope=shared").json()["memories"]
    assert any(m["content"] == cand["content"] for m in mems)


def test_memory_candidate_reject(client, invite, monkeypatch):
    register(client, invite, "mem04-reject@test.dev")
    chat_id, _ = setup_workspace(client)
    r = client.post(f"/api/chats/{chat_id}/messages", json={"content": "记住我用 pytest"})
    run_id = r.json()["run"]["id"]

    async def fake_complete(self, **kwargs):
        return '[{"title":"测试框架","content":"用户偏好用 pytest"}]'

    monkeypatch.setattr(RunExecutor, "_complete_text", fake_complete)
    with SessionLocal() as db:
        db.get(Run, run_id).status = "completed"
        db.commit()

    asyncio.run(RunExecutor(run_id)._generate_memory_candidates("已完成"))
    cand = client.get("/api/memories/candidates").json()["candidates"][0]

    assert client.post(f"/api/memories/candidates/{cand['id']}/reject").status_code == 200
    assert client.get("/api/memories/candidates").json()["candidates"] == []
    # 拒绝后不写入记忆
    mems = client.get("/api/memories?scope=shared").json()["memories"]
    assert not any(m["content"] == cand["content"] for m in mems)
