"""Tests for Health checks, Prometheus metrics, and Watchdog monitoring."""

from unittest.mock import patch

from fastapi.testclient import TestClient

from savageops.health import run_checks
from savageops.main import app
from savageops.metrics import ESCALATIONS, timed
from savageops.watchdog import send_alert


def test_health_run_checks_structure():
    import savageops.health as health_mod

    health_mod._checks_cache = None
    res = run_checks()
    assert isinstance(res, dict)
    assert "supabase" in res
    assert "github" in res
    assert "llm_gemini" in res
    assert "llm_mistral" in res
    assert "llm_groq" in res
    assert "llm_openrouter" in res
    assert "grafana" in res
    assert "loki" in res


def test_deep_health_does_not_call_llm(monkeypatch):
    import savageops.health as health_mod
    from savageops.auth import require_admin

    health_mod._checks_cache = None
    monkeypatch.setenv("GEMINI_API_KEY", "dummy-gemini")
    monkeypatch.setenv("GROQ_API_KEY", "dummy-groq")
    monkeypatch.setenv("OPENROUTER_API_KEY", "dummy-openrouter")
    monkeypatch.delenv("MISTRAL_API_KEY", raising=False)

    app.dependency_overrides[require_admin] = lambda: {"sub": "admin-test"}
    try:
        with (
            patch("openai.OpenAI", side_effect=AssertionError("OpenAI must not be called by /health/deep")),
            patch("savageops.llm.ChatOpenAI", side_effect=AssertionError("ChatOpenAI must not be called by /health/deep")),
        ):
            client = TestClient(app)
            r = client.get("/health/deep")
    finally:
        app.dependency_overrides.pop(require_admin, None)

    assert r.status_code == 200
    body = r.json()
    assert body["checks"]["llm_gemini"] == "configured"
    assert body["checks"]["llm_groq"] == "configured"
    assert body["checks"]["llm_openrouter"] == "configured"
    assert body["checks"]["llm_mistral"] == "skipped"


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


def test_sentinel_webhook_endpoint(monkeypatch):
    import savageops.config as cfg
    import savageops.sentinel.webhook as wh

    test_secret = "test-webhook-secret"
    monkeypatch.setattr(cfg, "WEBHOOK_SECRET", test_secret)
    monkeypatch.setattr(wh, "WEBHOOK_SECRET", test_secret)

    client = TestClient(app)

    # 1. Reject without auth header
    r = client.post("/api/webhook/grafana", json={"alerts": []})
    assert r.status_code == 401

    # 2. Reject with invalid secret
    r_bad = client.post(
        "/api/webhook/grafana",
        json={"alerts": []},
        headers={"Authorization": "Bearer wrong-secret"},
    )
    assert r_bad.status_code == 401

    # 3. Accept with valid secret
    r_ok = client.post(
        "/api/webhook/grafana",
        json={"alerts": []},
        headers={"Authorization": f"Bearer {test_secret}"},
    )
    assert r_ok.status_code == 202
    assert "created" in r_ok.json()
