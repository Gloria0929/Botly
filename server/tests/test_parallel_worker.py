"""实际 Worker 主循环的快速子任务调度，不依赖 Redis 或维护扫描。"""
import asyncio
from contextlib import contextmanager

from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app import worker
from app.core.db import Base
from app.core.models import Run


def test_mock_recognizes_renamed_writer():
    from app.agent.llm import _mock_decide, _stream_mock
    from app.tools.registry import BUILTIN_TOOLS, schema_list

    ctx = {"role": "worker", "bot_name": "撰稿人"}
    name, arguments = _mock_decide([], schema_list([BUILTIN_TOOLS["file_read"]]), ctx)
    assert name == "file_read" and arguments["path"] == "research.md"

    async def complete():
        return [chunk async for chunk in _stream_mock(messages=[], tools=[], ctx=ctx)]

    assert "report.md" in asyncio.run(complete())[-1]["content"]


def test_worker_starts_queued_task_children_together_before_reconcile(client, monkeypatch):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)

    @contextmanager
    def scope():
        with Session(engine, expire_on_commit=False) as db:
            yield db
            db.commit()

    with scope() as db:
        children = [Run(user_id="worker-test", chat_id="room", kind="task", parent_run_id="parent") for _ in range(2)]
        db.add_all(children); db.flush()
        ids = {run.id for run in children}
    monkeypatch.setattr(worker, "session_scope", scope)
    monkeypatch.setattr(worker, "init_db", lambda: None)
    monkeypatch.setattr(worker, "_stop", False)
    monkeypatch.setattr(worker, "WORKER_CONCURRENCY", 2)
    monkeypatch.setattr(worker, "_recover_and_collect", lambda: [])
    monkeypatch.setattr(worker, "_pending_free_runs", lambda: [])
    monkeypatch.setattr(worker, "_wake_waiting_parents", lambda *args: [])
    monkeypatch.setattr(worker, "_run_prune", lambda: None)
    monkeypatch.setattr(worker, "_run_alert_check", lambda: None)
    monkeypatch.setattr(worker, "deliver_pending", lambda: None)
    monkeypatch.setattr(worker.terminal, "pending", lambda: [])
    monkeypatch.setattr(worker.signal, "signal", lambda *args: None)
    monkeypatch.setattr(worker.redis_bus, "available", lambda: False)

    async def dequeue(**kwargs):
        await asyncio.sleep(.01)
        return None

    monkeypatch.setattr(worker.redis_bus, "dequeue", dequeue)
    started = set()

    async def run():
        together = asyncio.Event()

        async def process(run_id):
            started.add(run_id)
            if started == ids:
                together.set()
            await asyncio.wait_for(together.wait(), 1)
            worker._stop = True

        monkeypatch.setattr(worker, "process_one", process)
        await asyncio.wait_for(worker.main(), 2)

    try:
        asyncio.run(run())
        assert started == ids
    finally:
        engine.dispose()
