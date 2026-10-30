"""8.6 运行状况看板：完成率、失败原因分布与最近失败。"""
from __future__ import annotations

from datetime import timedelta

from app.db import SessionLocal
from app.models import Run, now
from tests.conftest import register


def test_stats_overview(client, invite):
    data = register(client, invite, "stats@test.dev")
    with SessionLocal() as db:
        base = dict(user_id=data["id"], chat_id=data["chat_id"], mode="auto", config_snapshot={})
        db.add(Run(status="completed", steps_used=6, tokens_used=1200, started_at=now() - timedelta(minutes=2), ended_at=now() - timedelta(minutes=1), end_reason="已完成", **base))
        db.add(Run(status="completed", steps_used=4, tokens_used=800, started_at=now() - timedelta(minutes=4), ended_at=now() - timedelta(minutes=3), end_reason="已完成", **base))
        db.add(Run(status="failed", end_reason="模型调用失败：HTTP 401", ended_at=now() - timedelta(minutes=5), **base))
        db.add(Run(status="failed", end_reason="模型调用失败：连接超时", ended_at=now() - timedelta(minutes=6), **base))
        db.add(Run(status="stopped", end_reason="用户停止", ended_at=now() - timedelta(minutes=7), **base))
        db.commit()

    r = client.get("/api/stats/overview?days=7")
    assert r.status_code == 200, r.text
    body = r.json()
    totals = body["totals"]
    assert totals["runs"] == 5
    assert totals["completed"] == 2
    assert totals["failed"] == 2
    assert totals["stopped"] == 1
    assert abs(totals["success_rate"] - 0.4) < 1e-6
    assert totals["avg_duration_sec"] == 60.0
    assert len(body["daily"]) == 7
    assert body["failures"][0] == {"reason": "模型调用失败", "count": 2}
    assert len(body["recent_failures"]) == 2
    assert "alerts" in body and "queue" in body
