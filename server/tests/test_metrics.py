"""8.6 可观测性：/api/metrics 的鉴权与输出。"""
from __future__ import annotations

from app.api import metrics as metrics_module
from tests.conftest import register


def test_metrics_denied_without_token_or_login(client):
    client.cookies.clear()
    assert client.get("/api/metrics").status_code == 403


def test_metrics_with_bearer_token(client, monkeypatch, invite):
    client.cookies.clear()
    monkeypatch.setattr(metrics_module, "METRICS_TOKEN", "secret-token")
    r = client.get("/api/metrics", headers={"Authorization": "Bearer secret-token"})
    assert r.status_code == 200, r.text
    body = r.text
    for name in (
        "botly_runs_active",
        "botly_queue_depth",
        "botly_approvals_pending",
        "botly_questions_pending",
        "botly_users_total",
        "botly_usage_calls_total",
        "botly_usage_errors_total",
        'botly_usage_tokens_total{kind="input"}',
        'botly_usage_tokens_total{kind="output"}',
    ):
        assert name in body, name


def test_metrics_for_logged_in_user(client, invite):
    register(client, invite, "metrics-user@test.dev")
    r = client.get("/api/metrics")
    assert r.status_code == 200, r.text
    assert "botly_runs_active" in r.text
