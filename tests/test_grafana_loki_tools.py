"""Tests for Grafana and Loki tooling with LogQL injection protection."""

from types import SimpleNamespace
from unittest.mock import patch

import pytest

from savageops.tools.grafana import still_firing
from savageops.tools.loki import SAFE, get_logs


def test_safe_logql_service_regex():
    # Safe service names
    assert SAFE.match("dummy-app")
    assert SAFE.match("order_service")
    assert SAFE.match("auth.api-v2")

    # Injection attempts
    assert not SAFE.match('dummy" |= "')
    assert not SAFE.match("svc; DROP TABLE")
    assert not SAFE.match("svc}")


def test_logql_injection_rejection():
    malicious_alert = {
        "labels": {"service": 'dummy"} |= "malicious'},
        "startsAt": "2026-10-28T10:00:00Z",
    }
    with pytest.raises(ValueError, match="Invalid service label"):
        get_logs(malicious_alert)


def test_loki_fixture_fallback():
    sample_alert = {
        "labels": {"service": "dummy-app"},
        "startsAt": "2026-10-28T10:00:00Z",
    }
    with patch("savageops.tools.loki.GRAFANA_URL", ""), patch("savageops.tools.loki.LOKI_URL", ""):
        logs = get_logs(sample_alert)
        assert len(logs) > 0
        assert "Traceback" in logs or "Error" in logs


def test_loki_query_does_not_filter_traceback_frames():
    response = SimpleNamespace(
        raise_for_status=lambda: None,
        json=lambda: {"data": {"result": []}},
    )
    with patch("savageops.tools.loki.GRAFANA_URL", "https://grafana.example"), \
         patch("savageops.tools.loki.LOKI_URL", ""), \
         patch("savageops.tools.loki.httpx.get", return_value=response) as get:
        get_logs({"labels": {"service": "dummy-app"}, "startsAt": "2026-10-28T10:00:00Z"})
    assert get.call_args.kwargs["params"]["query"] == '{service="dummy-app"}'


def test_still_firing_safe_fallback():
    # When Grafana is unreachable, still_firing returns True as a safe default
    with patch("httpx.get", side_effect=Exception("Connection refused")):
        assert still_firing("unknown-fingerprint") is True
