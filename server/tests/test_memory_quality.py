"""记忆质量优化回归：相似度去重、搜索、疑似重复整理、分类。"""
from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

from sqlalchemy import func, select

from app.core.db import SessionLocal
from app.core.models import Bot, Memory, Run, User
from app.core.similarity import duplicates_in, is_duplicate, normalize, similarity
from app.agent.orchestrator import RunExecutor
from app.tools.registry import _memory_save
from tests.conftest import register, seed_user_messages, setup_workspace


# ---- 纯函数：相似度 ----

def test_normalize():
    assert normalize("AＢＣ１２３") == "abc123"
    assert normalize("你好，世界！") == "你好世界"
    assert normalize("  Hello,  World  ") == "helloworld"


def test_similarity():
    assert similarity("用户偏好简洁中文", "用户偏好简洁中文") == 1.0
    assert similarity("用户偏好简洁中文", "用户喜欢简洁的中文表达") > 0.5
    assert similarity("用户偏好简洁中文", "项目使用 Postgres 数据库") < 0.3


def test_is_duplicate():
    # 全角/标点差异 → 归一化后相等
    assert is_duplicate("用户偏好简洁的中文", "用户偏好简洁的中文！")
    # 短内容被长内容完整包含
    assert is_duplicate("用户偏好简洁的中文表达", "用户偏好简洁的中文表达，且不用感叹号")
    # 相似度达标（替换个别词但保留大量公共子序列）
    assert is_duplicate("用户偏好简洁中文", "用户偏好简洁的中文")
    # 不相关
    assert not is_duplicate("用户偏好简洁中文", "项目数据库用 Postgres")


def test_duplicates_in():
    items = [("a", "用户偏好简洁中文"), ("b", "用户喜欢简洁的中文"), ("c", "项目用 Postgres")]
    pairs = duplicates_in(items, threshold=0.5)
    ids = {frozenset((items[i][0], items[j][0])) for i, j, _ in pairs}
    assert frozenset(("a", "b")) in ids


# ---- 分类字段 ----

def test_memory_category_roundtrip(client, invite):
    register(client, invite, "cat@test.dev")
    r = client.post(
        "/api/memories",
        json={"scope": "shared", "title": "偏好", "content": "简洁中文", "category": "偏好"},
    )
    assert r.status_code == 201, r.text
    assert r.json()["category"] == "偏好"
    mems = client.get("/api/memories?scope=shared").json()["memories"]
    assert any(m["category"] == "偏好" for m in mems)


# ---- 搜索 ----

def test_memory_search(client, invite):
    register(client, invite, "search@test.dev")
    client.post("/api/memories", json={"scope": "shared", "content": "用户偏好简洁中文"})
    client.post("/api/memories", json={"scope": "shared", "content": "项目使用 Postgres 数据库"})
    r = client.get("/api/memories?scope=shared&q=简洁")
    assert r.status_code == 200
    mems = r.json()["memories"]
    assert len(mems) == 1 and "简洁" in mems[0]["content"]
    # 通配符转义：% 不作为通配符，不应报错也不应返回全部
    r2 = client.get("/api/memories?scope=shared&q=%25")
    assert r2.status_code == 200
    assert r2.json()["memories"] == []


# ---- 疑似重复整理 ----

def test_memory_duplicates_endpoint(client, invite):
    register(client, invite, "dup@test.dev")
    client.post("/api/memories", json={"scope": "shared", "content": "用户偏好简洁的中文表达"})
    client.post("/api/memories", json={"scope": "shared", "content": "用户喜欢简洁的中文风格"})
    r = client.get("/api/memories/duplicates?scope=shared")
    assert r.status_code == 200
    body = r.json()
    assert body["count"] >= 1, body
    assert body["duplicates"][0]["score"] > 0.5


# ---- memory_save 相似度去重 ----

def test_memory_save_dedup(client, invite):
    register(client, invite, "msave@test.dev")
    chat_id, _ = setup_workspace(client)
    seed_user_messages(chat_id, ["我喜欢简洁的中文表达", "回复时请保持中文简洁风格"])
    run_id = client.post(f"/api/chats/{chat_id}/messages", json={
        "content": "记住，以后回答都用简洁的中文表达",
    }).json()["run"]["id"]
    with SessionLocal() as db:
        user = db.scalars(select(User).where(User.email == "msave@test.dev")).one()
        bot = db.scalars(select(Bot).where(Bot.user_id == user.id)).first()
        assert bot is not None
        ctx = SimpleNamespace(
            bot=bot, db=db, run=db.get(Run, run_id), user_id=user.id, chat_id=chat_id,
            assert_lease=lambda: None,
        )
        r1 = asyncio.run(_memory_save(ctx, "记住，以后回答都用简洁的中文表达"))
        assert "已保存" in r1, r1
        # 加字/近义表述（高度相似）应被去重拦截
        r2 = asyncio.run(_memory_save(ctx, "记住，以后回答都用简洁的中文表达风格"))
        assert "无需重复" in r2, r2
        db.rollback()


# ---- 候选去重：候选之间高度相似 ----

def test_candidate_dedup_similar(client, invite, monkeypatch):
    register(client, invite, "cand-dup@test.dev")
    chat_id, _ = setup_workspace(client)
    source_text = "以后回答尽量简洁"
    source_ids = seed_user_messages(chat_id, [source_text, source_text])
    r = client.post(f"/api/chats/{chat_id}/messages", json={"content": source_text})
    run_id = r.json()["run"]["id"]
    source_ids.append(r.json()["message"]["id"])

    async def fake_complete(self, **kwargs):
        evidence = [{"message_id": source_id, "quote": source_text} for source_id in source_ids]
        return json.dumps([
            {"title": "偏好", "content": "用户偏好简洁的中文表达", "evidence": evidence},
            {"title": "偏好2", "content": "用户偏好简洁的中文表达风格", "evidence": evidence},
        ])

    monkeypatch.setattr(RunExecutor, "_complete_text", fake_complete)
    with SessionLocal() as db:
        db.get(Run, run_id).status = "completed"
        db.commit()

    asyncio.run(RunExecutor(run_id)._generate_memory_candidates("已完成"))
    body = client.get("/api/memories/candidates").json()
    # 两条候选高度相似，只应保留一条
    assert len(body["candidates"]) == 1, body


# ---- 候选去重：与现有记忆相似 ----

def test_candidate_dedup_existing(client, invite, monkeypatch):
    register(client, invite, "cand-exist@test.dev")
    chat_id, _ = setup_workspace(client)
    client.post("/api/memories", json={"scope": "shared", "content": "用户偏好简洁的中文表达"})
    source_text = "记住我喜欢简洁的中文表达"
    source_ids = seed_user_messages(chat_id, [source_text, source_text])
    r = client.post(f"/api/chats/{chat_id}/messages", json={"content": source_text})
    run_id = r.json()["run"]["id"]
    source_ids.append(r.json()["message"]["id"])

    async def fake_complete(self, **kwargs):
        evidence = [{"message_id": source_id, "quote": source_text} for source_id in source_ids]
        return json.dumps([{"title": "偏好", "content": "用户偏好简洁的中文表达风格",
                            "evidence": evidence}])

    monkeypatch.setattr(RunExecutor, "_complete_text", fake_complete)
    with SessionLocal() as db:
        db.get(Run, run_id).status = "completed"
        db.commit()

    asyncio.run(RunExecutor(run_id)._generate_memory_candidates("已完成"))
    body = client.get("/api/memories/candidates").json()
    # 与现有记忆高度相似，不应再生成候选
    assert body["candidates"] == [], body


def test_automatic_memory_save_needs_three_distinct_matching_messages(client, invite):
    register(client, invite, "msave-repeat@test.dev")
    chat_id, _ = setup_workspace(client)
    preference = "以后回答请保持简洁的中文表达。"
    seed_user_messages(chat_id, [preference, preference])
    run_id = client.post(f"/api/chats/{chat_id}/messages", json={"content": preference}).json()["run"]["id"]
    with SessionLocal() as db:
        user = db.scalars(select(User).where(User.email == "msave-repeat@test.dev")).one()
        bot = db.scalar(select(Bot).where(Bot.user_id == user.id))
        run = db.get(Run, run_id)
        ctx = SimpleNamespace(bot=bot, db=db, run=run, user_id=user.id, chat_id=chat_id,
                              assert_lease=lambda: None)
        accepted = asyncio.run(_memory_save(ctx, "用户偏好简洁的中文表达。"))
        assert "已保存" in accepted
        changed_topic = asyncio.run(_memory_save(ctx, "用户偏好使用 Postgres 数据库。"))
        assert "3 条不同消息" in changed_topic
        assert db.scalar(select(func.count(Bot.id)).where(Bot.id == bot.id)) == 1
        assert db.scalar(select(func.count(Memory.id)).where(Memory.run_id == run_id)) == 1
