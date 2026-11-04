"""事件在提交阶段分配序号；用真实 Postgres 验证乱序提交不会丢事件。"""
import os
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest
from sqlalchemy import create_engine, event, func, select
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

from app.core.db import SessionLocal
from app.core.events import emit
from app.core.models import Event


def test_uncommitted_events_are_discarded_on_close(client):
    db = SessionLocal()
    queued = emit(db, "closed-event-user", None, "test.closed", {"value": "discard"})
    assert queued.id is None
    db.close()
    db.commit()
    db.close()
    with SessionLocal() as reader:
        assert reader.scalar(select(func.count()).select_from(Event).where(Event.type == "test.closed")) == 0


@pytest.fixture
def postgres():
    url = os.getenv("BOTLY_TEST_POSTGRES_URL")
    if not url:
        pytest.skip("使用独立 BOTLY_TEST_POSTGRES_URL 执行 Postgres 并发回归")
    assert (make_url(url).database or "").startswith("botly_test_"), "拒绝使用业务数据库"
    engine = create_engine(url, connect_args={"connect_timeout": 5})
    Event.__table__.create(engine, checkfirst=True)
    yield engine
    engine.dispose()


def test_late_commit_receives_an_id_after_the_visible_cursor(postgres):
    user_id = "event-late-commit"
    with Session(postgres) as slow, Session(postgres) as fast:
        first = emit(slow, user_id, "chat", "test.slow", {"name": "slow"})
        second = emit(fast, user_id, "chat", "test.fast", {"name": "fast"})
        fast.commit()
        with Session(postgres) as reader:
            cursor = reader.scalar(select(func.max(Event.id)).where(Event.user_id == user_id))
        slow.commit()
        with Session(postgres) as reader:
            replay = reader.scalars(select(Event).where(Event.user_id == user_id, Event.id > cursor)).all()
        assert first.id > second.id == cursor
        assert [row.payload["name"] for row in replay] == ["slow"]


def test_concurrent_event_inserts_wait_for_the_preceding_commit(postgres):
    inserted, release, second_queued = threading.Event(), threading.Event(), threading.Event()
    user_id = "event-concurrent-commit"

    def pause_first(conn, cursor, statement, parameters, context, many):
        if threading.current_thread().name.endswith("_0") and statement.startswith("INSERT INTO events"):
            inserted.set()
            assert release.wait(3)

    def commit_first():
        with Session(postgres) as db:
            item = emit(db, user_id, "chat", "test.first", {})
            db.commit()
            return item.id

    def commit_second():
        with Session(postgres) as db:
            item = emit(db, user_id, "chat", "test.second", {})
            second_queued.set()
            db.commit()
            return item.id

    event.listen(postgres, "after_cursor_execute", pause_first)
    try:
        with ThreadPoolExecutor(max_workers=2, thread_name_prefix="event-order") as pool:
            first = pool.submit(commit_first)
            assert inserted.wait(3)
            second = pool.submit(commit_second)
            try:
                assert second_queued.wait(3)
                with Session(postgres) as reader:
                    visible = reader.scalars(select(Event).where(Event.user_id == user_id)).all()
                    assert visible == []
            finally:
                release.set()
            assert first.result(timeout=3) < second.result(timeout=3)
    finally:
        release.set()
        event.remove(postgres, "after_cursor_execute", pause_first)
