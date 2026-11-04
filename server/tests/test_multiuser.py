"""多用户数据隔离、群聊列表查询规模和执行配额。"""
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

from sqlalchemy import event, select

from app import worker
from app.api import logs
from app.core.db import SessionLocal, engine
from app.core.logging_setup import fingerprint
from app.core.models import Run, now
from tests.conftest import login, register, setup_workspace


def test_chat_list_queries_do_not_grow_per_chat(client, invite):
    register(client, invite, "multi-query@test.dev")
    _, bots = setup_workspace(client)
    for i in range(8):
        r = client.post("/api/chats", json={"name": f"群聊 {i}", "member_ids": [b["id"] for b in bots]})
        assert r.status_code == 201
    statements = []
    def capture(conn, cursor, statement, parameters, context, executemany):
        if statement.lstrip().upper().startswith("SELECT"):
            statements.append(statement)
    event.listen(engine, "before_cursor_execute", capture)
    try:
        rows = client.get("/api/chats").json()
    finally:
        event.remove(engine, "before_cursor_execute", capture)
    assert len(rows) == 9
    assert all(row["member_count"] == 3 for row in rows)
    assert len(statements) <= 6, statements


def test_metrics_logs_and_notifications_are_owned(client, invite, tmp_path, monkeypatch):
    alice = register(client, invite, "multi-alice@test.dev")
    alice_chat, alice_bots = setup_workspace(client)
    alice_run = client.post(f"/api/chats/{alice_chat}/messages", json={"content": "私有任务"}).json()["run"]["id"]
    bob = register(client, invite, "multi-bob@test.dev")
    bob_chat, _ = setup_workspace(client)
    bob_run = client.post(f"/api/chats/{bob_chat}/messages", json={"content": "Bob 任务"}).json()["run"]["id"]
    p = tmp_path / "app.log"
    p.write_text(
        f"2026-11-03 10:00:00 INFO    pid=1 [agent] req=- run={alice_run} task=- Alice private\n"
        f"2026-11-03 10:00:01 INFO    pid=1 [agent] req=- run={bob_run} task=- Bob private\n"
        "2026-11-03 10:00:02 INFO    pid=1 [worker] req=- run=- task=- Deployment data\n"
        f"2026-11-03 10:00:03 INFO    pid=1 [access] req=r run=- task=- GET /api/bots uid={fingerprint(bob['id'])}\n",
        encoding="utf-8")
    monkeypatch.setattr(logs, "_log_files", lambda: [p])
    entries = client.get("/api/logs").json()["items"]
    assert {entry["module"] for entry in entries} == {"agent", "access"}
    assert not any("Alice" in entry["message"] for entry in entries)
    assert {m["module"] for m in client.get("/api/logs/modules").json()} == {"agent", "access"}
    metric_text = client.get("/api/metrics").text
    assert "botly_users_total 1" in metric_text
    assert 'botly_runs{status="queued"} 1' in metric_text
    for path in (f"/api/chats/{alice_chat}/messages", f"/api/runs/{alice_run}", f"/api/bots/{alice_bots[0]['id']}"):
        assert client.get(path).status_code == 404
    assert client.get("/api/stats/overview").json()["alerts"] == []


def test_multiple_workers_respect_global_and_personal_capacity(client, invite, monkeypatch):
    users = [register(client, invite, f"multi-claim-{i}@test.dev") for i in range(3)]
    ids = []
    with SessionLocal() as db:
        for u in users:
            for _ in range(3):
                run = Run(user_id=u["id"], chat_id=u["chat_id"], status="queued", config_snapshot={})
                db.add(run)
                db.flush()
                ids.append(run.id)
        db.commit()
    monkeypatch.setattr(worker, "MAX_CONCURRENT_RUNS", 4)
    monkeypatch.setattr(worker, "MAX_CONCURRENT_RUNS_PER_USER", 2)
    # 共享测试库的其他用例可能遗留执行；本测试只统计新建的这些用户。
    with SessionLocal() as db:
        for run in db.scalars(select(Run).where(Run.status == "running", Run.id.not_in(ids))):
            run.status = "stopped"
        db.commit()
    with ThreadPoolExecutor(max_workers=8) as pool:
        claimed = list(pool.map(lambda rid: worker._claim(rid, "test-worker"), ids))
    assert sum(bool(token) for token in claimed) == 4
    with SessionLocal() as db:
        active = list(db.scalars(select(Run).where(Run.id.in_(ids), Run.status == "running")))
        assert all(sum(r.user_id == u["id"] for r in active) <= 2 for u in users)
        for r in active:
            r.status = "stopped"
        db.commit()
