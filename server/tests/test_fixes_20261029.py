"""2026-10-29 审计修复的回归测试：@ 标点、群聊限额、保留期清理、文件删除。"""
from __future__ import annotations

import asyncio
from datetime import timedelta

from app.db import SessionLocal
from app.models import Chat, Event, FileRec, UsageLog, now
from tests.conftest import register, setup_workspace


def test_at_bot_with_chinese_punctuation_is_direct(client, invite):
    register(client, invite, "mention-punct@test.dev")
    chat_id, bots = setup_workspace(client)
    researcher = next(b for b in bots if b["name"] == "研究员")

    for content in ("@研究员，简单介绍一句。", "@研究员：你好"):
        run = client.post(f"/api/chats/{chat_id}/messages", json={"content": content}).json()["run"]
        assert run["target_bot_id"] == researcher["id"], content
        # 不要留在共享测试库里干扰其他用例
        client.post(f"/api/runs/{run['id']}/stop")


def test_chat_limit_override_is_validated(client, invite):
    register(client, invite, "chat-limits@test.dev")
    chat_id, _ = setup_workspace(client)

    # 运行时长限制已删除，旧字段按未知限额拒绝
    assert client.patch(
        f"/api/chats/{chat_id}", json={"limits_override": {"max_duration_min": 120}}
    ).status_code == 400
    assert client.patch(
        f"/api/chats/{chat_id}", json={"limits_override": {"token_budget": 10}}
    ).status_code == 400
    assert client.patch(
        f"/api/chats/{chat_id}", json={"limits_override": {"unknown": 1}}
    ).status_code == 400

    ok = client.patch(
        f"/api/chats/{chat_id}",
        json={"limits_override": {"token_budget": 0}},
    )
    assert ok.status_code == 200, ok.text
    limits = client.get(f"/api/chats/{chat_id}").json()["limits"]
    assert limits["token_budget"] == 0
    assert "max_duration_min" not in limits


def test_bot_patch_rejects_empty_name(client, invite):
    register(client, invite, "bot-name@test.dev")
    _, bots = setup_workspace(client)
    r = client.patch(f"/api/bots/{bots[0]['id']}", json={"name": ""})
    assert r.status_code == 422


def test_retention_prune_keeps_token_stats(client):
    with SessionLocal() as db:
        event = Event(
            user_id="retention-user",
            chat_id=None,
            type="old",
            payload={},
            created_at=now() - timedelta(days=8),
        )
        log = UsageLog(
            user_id="retention-user",
            input_tokens=10,
            output_tokens=5,
            latency_ms=321,
            error="boom",
            created_at=now() - timedelta(days=31),
        )
        db.add_all([event, log])
        db.commit()
        event_id, log_id = event.id, log.id

    from app.maintenance import prune_expired

    with SessionLocal() as db:
        prune_expired(db)
        db.commit()

    with SessionLocal() as db:
        assert db.get(Event, event_id) is None
        kept = db.get(UsageLog, log_id)
        assert kept is not None
        assert kept.input_tokens == 10 and kept.output_tokens == 5
        assert kept.error is None and kept.latency_ms == 0


def test_file_delete_removes_file_record(client, invite):
    register(client, invite, "file-delete@test.dev")
    chat_id, _ = setup_workspace(client)
    from app import sandbox
    from app.tools import _file_delete

    with SessionLocal() as db:
        user_id = db.get(Chat, chat_id).user_id
        sandbox.write_file(user_id, chat_id, "ghost.txt", "hi")
        db.add(FileRec(user_id=user_id, chat_id=chat_id, path="ghost.txt", size=2))
        db.commit()

        class Ctx:
            pass

        ctx = Ctx()
        ctx.db, ctx.user_id, ctx.chat_id = db, user_id, chat_id
        asyncio.run(_file_delete(ctx, "ghost.txt"))
        db.commit()

    assert client.get(f"/api/chats/{chat_id}/files").json() == []


def test_short_api_key_is_not_echoed():
    from app.api.providers import _last4

    assert _last4("abcd") is None
    assert _last4("abcdef") == "cdef"
    assert _last4(None) is None
