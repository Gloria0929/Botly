"""独立 Worker 的兜底轮询与后台收尾回归。"""
import asyncio
from datetime import timedelta

from app import worker
from app.db import SessionLocal
from app.models import Bot, Question, Run, now
from sqlalchemy import select
from tests.conftest import register, setup_workspace


def test_worker_executes_without_redis(client, invite, monkeypatch):
    register(client, invite, "worker-db-fallback@test.dev")
    chat_id, _ = setup_workspace(client)
    run_id = client.post(f"/api/chats/{chat_id}/messages", json={"content": "测试数据库轮询"}).json()["run"]["id"]

    async def no_queue(**kw):
        return None

    monkeypatch.setattr(worker, "_stop", False)
    monkeypatch.setattr(worker.redis_bus, "dequeue", no_queue)
    monkeypatch.setattr(worker.redis_bus, "enqueue", lambda _: False)
    monkeypatch.setattr(worker.signal, "signal", lambda *args: None)
    collect = worker._recover_and_collect
    # 共享测试库中其他用例故意留下了 queued 执行；--once 只应消费本用例的任务。
    monkeypatch.setattr(worker, "_recover_and_collect", lambda: [rid for rid in collect() if rid == run_id])
    asyncio.run(worker.main(once=True))
    result = client.get(f"/api/runs/{run_id}").json()
    assert result["status"] == "waiting", result
    assert result["resume"]["kind"] == "plan"


def test_worker_reconcile_expires_waiting_runs(client, invite):
    register(client, invite, "worker-expiry@test.dev")
    chat_id, _ = setup_workspace(client)
    run_id = client.post(f"/api/chats/{chat_id}/messages", json={"content": "测试过期回收"}).json()["run"]["id"]
    with SessionLocal() as db:
        run = db.get(Run, run_id)
        run.status = "waiting"
        run.resume_payload = {"kind": "question"}
        bot = db.scalar(select(Bot).where(Bot.user_id == run.user_id))
        db.add(Question(user_id=run.user_id, run_id=run.id, bot_id=bot.id, question="确认？", expires_at=now()-timedelta(seconds=1)))
        db.commit()
    worker._recover_and_collect()
    result = client.get(f"/api/runs/{run_id}").json()
    assert result["status"] == "failed" and "提问过期" in result["end_reason"]
    assert result["resume"] is None


def test_empty_event_stream_opens_then_delivers_new_events(client, invite):
    from app.api.stream import _stream
    from app.events import emit
    from app.models import Chat

    register(client, invite, "empty-sse-live@test.dev")
    chat_id, _ = setup_workspace(client)
    snapshot = client.get(f"/api/chats/{chat_id}/messages").json()
    with SessionLocal() as db:
        user_id = db.get(Chat, chat_id).user_id

    class Connected:
        async def is_disconnected(self):
            return False

    async def check_live():
        stream = _stream(user_id, chat_id, snapshot["event_cursor"], Connected())
        try:
            assert await asyncio.wait_for(anext(stream), 1) == ": connected\n\n"
            pending = asyncio.create_task(anext(stream))
            await asyncio.sleep(0)
            with SessionLocal() as db:
                emit(db, user_id, chat_id, "run.status", {"run_id": "new", "status": "completed"})
                db.commit()
            event = await asyncio.wait_for(pending, 2)
            assert "event: run.status" in event and '"status": "completed"' in event
        finally:
            await stream.aclose()
    asyncio.run(check_live())
