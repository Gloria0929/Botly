"""队首超过一页的自由群聊等待项不能挡住其它可执行工作。"""
from contextlib import contextmanager
from datetime import timedelta

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app import worker
from app.core.db import Base
from app.core.models import Chat, Message, Run, Thread, User, now


def test_fallback_scans_continue_past_fifty_blocked_free_children(client, monkeypatch):
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)

    @contextmanager
    def scope():
        with Session(engine, expire_on_commit=False) as db:
            yield db
            db.commit()

    monkeypatch.setattr(worker, "session_scope", scope)
    with scope() as db:
        user = User(email="queue-audit@test.dev", password_hash="unused", name="Queue")
        db.add(user); db.flush()
        chat = Chat(user_id=user.id, name="blocked room", mode="free")
        db.add(chat); db.flush()
        thread = Thread(user_id=user.id, chat_id=chat.id, type="main")
        db.add(thread); db.flush()
        created = now() - timedelta(minutes=10)
        blocked = []
        for index in range(52):
            message = Message(user_id=user.id, thread_id=thread.id, seq=index+1, sender_type="user", type="text", content="question")
            db.add(message); db.flush()
            root = Run(user_id=user.id, chat_id=chat.id, kind="free", status="waiting_children",
                       trigger_message_id=message.id, config_snapshot={"free": {"direct_started": True}})
            db.add(root); db.flush()
            child = Run(user_id=user.id, chat_id=chat.id, kind="free", status="waiting" if index == 0 else "queued",
                        target_bot_id="same-member", parent_run_id=root.id, trigger_message_id=message.id,
                        created_at=created + timedelta(seconds=index))
            db.add(child); db.flush()
            if index:
                blocked.append(child.id)
        ready_free = Run(user_id=user.id, chat_id=chat.id, kind="free", parent_run_id=root.id,
                         trigger_message_id=message.id, target_bot_id="other-member", created_at=now())
        ready_task = Run(user_id=user.id, chat_id=chat.id, kind="task", created_at=now())
        db.add_all([ready_free, ready_task]); db.flush()
        free_id, task_id = ready_free.id, ready_task.id
    try:
        collected = worker._recover_and_collect()
        assert free_id in collected and task_id in collected
        assert not set(blocked).intersection(collected)
        assert worker._pending_free_runs() == [free_id]
    finally:
        engine.dispose()
