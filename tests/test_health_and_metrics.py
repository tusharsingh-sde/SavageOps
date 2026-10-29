"""Tests for Health checks, Prometheus metrics, and Watchdog monitoring."""

from unittest.mock import patch

from fastapi.testclient import TestClient

from savageops.health import run_checks
from savageops.main import app
from savageops.metrics import ESCALATIONS, timed
from savageops.watchdog import send_alert


def test_health_run_checks_structure():
    res = run_checks()
    assert isinstance(res, dict)
    assert "supabase" in res
    assert "github" in res
    assert "openai" in res
    assert "grafana" in res
    assert "loki" in res


def test_metrics_instrumentation():
    initial_escalations = ESCALATIONS._value.get()
    ESCALATIONS.inc()
    assert ESCALATIONS._value.get() == initial_escalations + 1

    @timed("test_agent")
    def dummy_task():
        return 42

    assert dummy_task() == 42


def test_watchdog_alert_dispatcher():
    with patch("httpx.post") as mock_post:
        with patch(
            "savageops.watchdog.SLACK_WEBHOOK_URL", "https://hooks.slack.com/services/dummy"
        ):
            send_alert("Test incident is stuck")
            mock_post.assert_called_once()


def test_sentinel_webhook_endpoint():
    client = TestClient(app)

    # 1. Reject without auth header
    r = client.post("/api/webhook/grafana", json={"alerts": []})
    assert r.status_code == 401

    # 2. Accept with valid secret
    from savageops.config import WEBHOOK_SECRET

    r_ok = client.post(
        "/api/webhook/grafana",
        json={"alerts": []},
        headers={"Authorization": f"Bearer {WEBHOOK_SECRET}"},
    )
    assert r_ok.status_code == 202
    assert "created" in r_ok.json()
