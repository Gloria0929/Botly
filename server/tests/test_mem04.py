"""MEM-04：执行结束后生成记忆候选，用户确认后才写入。"""
from __future__ import annotations

import asyncio

from app.core.db import SessionLocal
from app.core.models import MemoryCandidate, Run
from app.agent.orchestrator import RunExecutor
from app.agent.summary_memory import _parse_memory_candidates
from tests.conftest import register, seed_user_messages, setup_workspace


def test_parse_memory_candidates():
    assert _parse_memory_candidates(
        '[{"title":"偏好","content":"喜欢简洁中文"}]'
    ) == [{"title": "偏好", "content": "喜欢简洁中文", "category": "", "observation_id": "", "evidence": []}]
    assert _parse_memory_candidates(
        '[{"title":"偏好","content":"喜欢简洁中文","category":"偏好"}]'
    ) == [{"title": "偏好", "content": "喜欢简洁中文", "category": "偏好", "observation_id": "", "evidence": []}]
    assert _parse_memory_candidates(
        '```json\n[{"content":"不用感叹号"}]\n```'
    ) == [{"title": "", "content": "不用感叹号", "category": "", "observation_id": "", "evidence": []}]
    assert _parse_memory_candidates("没有值得记住的") == []
    assert _parse_memory_candidates("[]") == []
    # 最多 3 条
    raw = "[" + ",".join(f'{{"content":"第{i}条"}}' for i in range(6)) + "]"
    assert len(_parse_memory_candidates(raw)) == 3


def test_memory_candidate_generation_and_accept(client, invite, monkeypatch):
    register(client, invite, "mem04@test.dev")
    chat_id, _ = setup_workspace(client)
    source_ids = seed_user_messages(chat_id, ["以后回答尽量简洁", "回复我时请用简洁的表达"])
    last_quote = "请继续简明扼要地回答"
    r = client.post(f"/api/chats/{chat_id}/messages", json={"content": last_quote})
    run_id = r.json()["run"]["id"]
    source_ids.append(r.json()["message"]["id"])

    async def fake_complete(self, **kwargs):
        import json
        evidence = [
            {"message_id": source_ids[0], "quote": "以后回答尽量简洁"},
            {"message_id": source_ids[1], "quote": "回复我时请用简洁的表达"},
                {"message_id": source_ids[2], "quote": last_quote},
        ]
        return json.dumps([{"title": "表达偏好", "content": "用户偏好简洁的中文表达", "evidence": evidence}])

    monkeypatch.setattr(RunExecutor, "_complete_text", fake_complete)
    with SessionLocal() as db:
        db.get(Run, run_id).status = "completed"
        db.commit()

    executor = RunExecutor(run_id)
    data = executor._load_memory_candidate_inputs()
    assert data is not None, (run_id, client.get(f"/api/runs/{run_id}").json())
    assert len(data["sources"]) == 3, data["sources"]
    asyncio.run(executor._generate_memory_candidates("已完成"))

    body = client.get("/api/memories/candidates").json()
    assert len(body["candidates"]) == 1, body
    cand = body["candidates"][0]
    assert "简洁" in cand["content"]
    assert cand["status"] == "pending"
    assert cand["evidence_count"] == 3
    assert len(cand["evidence"]) == 3

    assert client.get("/api/memories/candidates?status=observing").status_code == 400

    accepted = client.post(f"/api/memories/candidates/{cand['id']}/accept")
    assert accepted.status_code == 201, accepted.text
    assert accepted.json()["content"] == cand["content"]

    # 已处理的候选不再出现在待确认列表
    assert client.get("/api/memories/candidates").json()["candidates"] == []
    mems = client.get("/api/memories?scope=shared").json()["memories"]
    assert any(m["content"] == cand["content"] for m in mems)


def test_memory_candidates_need_three_distinct_user_messages(client, invite, monkeypatch):
    register(client, invite, "mem04-gate@test.dev")
    chat_id, _ = setup_workspace(client)
    first = "以后请用中文回复，并尽量简洁。"
    second = "回答我的问题时优先说中文，内容不要太长。"
    first_ids = seed_user_messages(chat_id, [first, second])
    single = client.post(f"/api/chats/{chat_id}/messages", json={"content": "先看看这个代码"}).json()
    single_id = single["run"]["id"]
    calls = []

    async def insufficient_evidence(self, **kwargs):
        calls.append(kwargs)
        evidence = [
            {"message_id": first_ids[0], "quote": first},  # 重复引用只计一条。
            {"message_id": first_ids[0], "quote": first},
            {"message_id": first_ids[1], "quote": second},
            # 正文确实来自用户，但与候选说法无关，也不能充当依据。
            {"message_id": single["message"]["id"], "quote": "先看看这个代码"},
            {"message_id": "fake-message-id", "quote": "第三次支持该偏好"},
        ]
        return __import__("json").dumps([{"title": "表达偏好", "content": "用户喜欢简洁的中文回答",
                                          "evidence": evidence}])

    monkeypatch.setattr(RunExecutor, "_complete_text", insufficient_evidence)
    with SessionLocal() as db:
        db.get(Run, single_id).status = "completed"
        db.commit()
    asyncio.run(RunExecutor(single_id)._generate_memory_candidates("已完成"))
    assert len(calls) == 1
    assert client.get("/api/memories/candidates").json()["candidates"] == []
    with SessionLocal() as db:
        observation = db.query(MemoryCandidate).filter(
            MemoryCandidate.chat_id == chat_id,
            MemoryCandidate.status == "observing",
        ).one()
        assert observation.status == "observing" and len(observation.evidence) == 2

    third = "今后给我中文的短回答。"
    run = client.post(f"/api/chats/{chat_id}/messages", json={"content": third}).json()
    evidence_sources = [*first_ids, run["message"]["id"]]
    with SessionLocal() as db:
        db.get(Run, run["run"]["id"]).status = "completed"
        db.commit()

    async def enough_evidence(self, **kwargs):
        evidence = [{"message_id": source_id, "quote": quote} for source_id, quote in
                    zip(evidence_sources, (first, second, third))]
        return __import__("json").dumps([{"title": "表达偏好", "content": "用户喜欢简洁的中文回答",
                                          "evidence": evidence}])

    monkeypatch.setattr(RunExecutor, "_complete_text", enough_evidence)
    asyncio.run(RunExecutor(run["run"]["id"])._generate_memory_candidates("已完成"))
    candidates = client.get("/api/memories/candidates").json()["candidates"]
    assert len(candidates) == 1 and candidates[0]["evidence_count"] == 3


def test_memory_candidate_reject(client, invite, monkeypatch):
    register(client, invite, "mem04-reject@test.dev")
    chat_id, _ = setup_workspace(client)
    source_ids = seed_user_messages(chat_id, ["我习惯用 pytest 写测试", "项目测试一直使用 pytest"])
    r = client.post(f"/api/chats/{chat_id}/messages", json={"content": "记住，以后继续使用 pytest 测试"})
    run_id = r.json()["run"]["id"]
    source_ids.append(r.json()["message"]["id"])

    async def fake_complete(self, **kwargs):
        import json
        evidence = [
            {"message_id": source_ids[0], "quote": "我习惯用 pytest 写测试"},
            {"message_id": source_ids[1], "quote": "项目测试一直使用 pytest"},
            {"message_id": source_ids[2], "quote": "记住，以后继续使用 pytest 测试"},
        ]
        return json.dumps([{"title": "测试框架", "content": "项目使用 pytest 测试", "evidence": evidence}])

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
