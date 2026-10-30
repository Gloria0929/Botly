"""8.6 告警：队列积压、执行卡死、失败率与冷却。"""
from __future__ import annotations

from datetime import timedelta

from app.alerts import check_alerts
from app.db import SessionLocal
from app.models import Run, now
from tests.conftest import register


def test_alerts_fire_and_cooldown(client, invite):
    data = register(client, invite, "alerts@test.dev")
    with SessionLocal() as db:
        user_id, chat_id = data["id"], data["chat_id"]
        base = dict(user_id=user_id, chat_id=chat_id, mode="auto", config_snapshot={})
        # 队列积压：12 个 queued，超过默认阈值 10
        for _ in range(12):
            db.add(Run(status="queued", **base))
        # 执行卡死：running 且开始于 30 分钟前
        db.add(
            Run(
                status="running",
                started_at=now() - timedelta(minutes=30),
                **base,
            )
        )
        # 失败率：窗口内 4 失败 + 1 完成 = 80%，超过 30%
        for _ in range(4):
            db.add(
                Run(
                    status="failed",
                    end_reason="模型调用失败：HTTP 401",
                    ended_at=now() - timedelta(minutes=5),
                    **base,
                )
            )
        db.add(
            Run(
                status="completed",
                end_reason="已完成",
                ended_at=now() - timedelta(minutes=5),
                **base,
            )
        )
        db.commit()

        fired = check_alerts(db)
        kinds = {a.kind for a in fired}
        assert kinds == {"queue_backlog", "run_stuck", "fail_rate"}, kinds

        # 冷却期内同类告警不重复写
        assert check_alerts(db) == []
