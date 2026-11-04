"""审查文档 P1–P3、D1：事件循环、流式写入和迁移回归。"""
import asyncio
import time
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, event, select, text

from app.agent import streaming
from app.agent.orchestrator import RunExecutor
from app.api import stream
from app.core import db as database
from app.core.db import SessionLocal, engine
from app.core.models import Event, Message
from app.tools import registry
from tests.test_run_reliability import _new_run


def test_stream_write_volume_is_linear_and_snapshots_include_tail(client, invite, monkeypatch):
    ctx = _new_run(client, invite, "linear-stream@test.dev")
    executor = RunExecutor(ctx["run_id"])
    state = executor._open_stream_message(ctx["main_id"], ctx["supervisor_id"])
    clock = [100.0]
    monkeypatch.setattr(streaming, "time", SimpleNamespace(monotonic=lambda: clock[0]))
    updates = []

    def capture(conn, cursor, statement, parameters, context, many):
        if statement.upper().startswith("UPDATE MESSAGES"):
            updates.append(statement)

    event.listen(engine, "before_cursor_execute", capture)
    try:
        content = ""
        for _ in range(100):
            content += "文" * 100
            clock[0] += 0.2
            executor._write_delta(state, content)
        snapshot = client.get(f"/api/chats/{ctx['chat_id']}/messages").json()
        assert next(m for m in snapshot["messages"] if m["id"] == state["id"])["content"] == content
        executor._finalize_message(state, content)
    finally:
        event.remove(engine, "before_cursor_execute", capture)
    with SessionLocal() as db:
        deltas = db.scalars(select(Event).where(Event.chat_id == ctx["chat_id"], Event.type == "message.delta")).all()
        assert len(deltas) == 99
        assert sum(len(e.payload["delta"]) for e in deltas) == 9900
        assert all("content" not in e.payload and len(e.payload["delta"]) == 100 for e in deltas)
        assert db.get(Message, state["id"]).content == content
    assert len(updates) <= 16, updates


def test_slow_sync_tool_does_not_block_other_runs(client, invite, monkeypatch):
    ctx = _new_run(client, invite, "threaded-file-tool@test.dev")
    executor = RunExecutor(ctx["run_id"])
    tool = registry.BUILTIN_TOOLS["file_list"]

    async def slow(ctx):
        time.sleep(0.15)
        return "files"

    monkeypatch.setattr(tool, "executor", slow)

    async def check():
        task = asyncio.create_task(executor._execute_builtin(tool, {}, ctx["supervisor_id"], None,
            thread_id=ctx["main_id"], call={"id": "slow-files"}))
        ticks = 0
        while not task.done():
            await asyncio.sleep(0.01)
            ticks += 1
        assert await task == "files"
        assert ticks >= 8

    asyncio.run(check())


def test_sse_fallback_uses_elapsed_time_and_keeps_database_off_loop(monkeypatch):
    stamp, queries = [0.0], []
    monkeypatch.setattr(stream, "time", SimpleNamespace(monotonic=lambda: stamp[0]))

    def read(*args):
        # 此函数必须由后台线程调用。
        with pytest.raises(RuntimeError):
            asyncio.get_running_loop()
        queries.append(stamp[0])
        return []

    class Waker:
        def __init__(self, *args):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def wait(self, timeout):
            # Redis 可能提前返回空响应；不能把完整 timeout 当成已等待时间。
            stamp[0] += min(timeout, 0.5)
            return False

    class Request:
        async def is_disconnected(self):
            return False

    monkeypatch.setattr(stream, "_read_events", read)
    monkeypatch.setattr(stream, "Waker", Waker)

    async def check():
        events = stream._stream("user", "chat", 0, Request())
        try:
            assert await anext(events) == ": connected\n\n"
            assert await anext(events) == ": ping\n\n"
            assert queries == [0.0, 10.0]
            assert stamp[0] == 15.0
        finally:
            await events.aclose()

    asyncio.run(check())


@pytest.fixture
def migration_engine(tmp_path, monkeypatch):
    isolated = create_engine(f"sqlite:///{tmp_path}/migration.db", connect_args={"check_same_thread": False})
    monkeypatch.setattr(database, "engine", isolated)
    monkeypatch.setattr(database, "IS_SQLITE", True)
    yield isolated
    isolated.dispose()


def test_fresh_migrations_are_serialized_and_restart_does_not_alter_schema(migration_engine, monkeypatch):
    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(lambda _: database.migrate_db(), range(2)))
    statements = []
    monkeypatch.setattr("app.core.config.APP_ENV", "production")

    def capture(conn, cursor, statement, parameters, context, many):
        statements.append(statement)

    event.listen(migration_engine, "before_cursor_execute", capture)
    try:
        database.init_db()
    finally:
        event.remove(migration_engine, "before_cursor_execute", capture)
    assert not any(s.lstrip().upper().startswith(("CREATE", "ALTER", "DROP", "UPDATE")) for s in statements)
    with migration_engine.begin() as conn:
        conn.execute(text("UPDATE alembic_version SET version_num='0008'"))
    with pytest.raises(RuntimeError, match="版本不匹配"):
        database.init_db()


def test_legacy_columns_are_migrated_without_stamping_head(migration_engine):
    database.migrate_db()
    with migration_engine.begin() as conn:
        conn.execute(text("DROP TABLE alembic_version"))
        conn.execute(text("ALTER TABLE messages DROP COLUMN reasoning_content"))
        conn.execute(text("ALTER TABLE threads DROP COLUMN seq_counter"))
    database.migrate_db()
    from sqlalchemy import inspect
    inspector = inspect(migration_engine)
    assert "reasoning_content" in {c["name"] for c in inspector.get_columns("messages")}
    assert "seq_counter" in {c["name"] for c in inspector.get_columns("threads")}
    with migration_engine.connect() as conn:
        assert conn.execute(text("SELECT version_num FROM alembic_version")).scalar() == "0012"
