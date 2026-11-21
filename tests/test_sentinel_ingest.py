"""Tests for Sentinel alert ingestion, deduplication, normalization, and debouncing."""

from datetime import datetime

import pytest

from savageops.sentinel.ingest import ingest, monitoring_down, normalize


def test_normalize_alert():
    raw_alert = {
        "status": "firing",
        "labels": {"alertname": "HighLatency", "service": "payment-api"},
        "annotations": {"summary": "p99 latency > 2s"},
        "startsAt": "2026-10-28T10:00:00Z",
        "fingerprint": "abc-123-def",
        "generatorURL": "http://grafana/alert/123",
        "extra_field": "ignore_me",
    }
    normalized = normalize(raw_alert)
    assert normalized["status"] == "firing"
    assert normalized["labels"]["service"] == "payment-api"
    assert normalized["fingerprint"] == "abc-123-def"
    assert "extra_field" not in normalized


def test_ingest_and_deduplication():
    alert = {
        "status": "firing",
        "labels": {"alertname": f"DeadlockTest-{datetime.now().timestamp()}", "service": "order-svc"},
        "startsAt": f"2026-10-28T12:00:{int(datetime.now().timestamp())}Z",
        "fingerprint": f"fp-deadlock-{datetime.now().timestamp()}",
    }
    # Ingest new alert
    inc_id = ingest(alert, source="webhook")
    assert inc_id is not None

    # Immediate second ingest with identical fingerprint:startsAt should be dropped as duplicate
    dup_id = ingest(alert, source="poller")
    assert dup_id is None


def test_monitoring_down():
    monitoring_down()  # Should create without unhandled error


@pytest.mark.asyncio
async def test_poll_loop_drops_alerts_without_savageops_label(monkeypatch):
    import asyncio
    from unittest.mock import AsyncMock, MagicMock

    from savageops.sentinel import poller

    alerts_payload = [
        {"status": "firing", "labels": {"alertname": "IgnoreFalse", "service": "s1", "savageops": "false"}, "fingerprint": "fp1"},
        {"status": "firing", "labels": {"alertname": "IgnoreMissing", "service": "s2"}, "fingerprint": "fp2"},
        {"status": "firing", "labels": {"alertname": "ProcessTrue", "service": "s3", "savageops": "true"}, "fingerprint": "fp3"},
    ]

    mock_resp = MagicMock()
    mock_resp.json.return_value = alerts_payload
    mock_resp.raise_for_status = MagicMock()

    call_count = 0

    async def mock_get(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count > 1:
            raise asyncio.CancelledError()
        return mock_resp

    mock_client = AsyncMock()
    mock_client.get = mock_get
    mock_client.__aenter__.return_value = mock_client
    mock_client.__aexit__.return_value = None

    monkeypatch.setattr(poller, "POLL_INTERVAL_SECONDS", 0.01)
    monkeypatch.setattr("httpx.AsyncClient", lambda **kwargs: mock_client)

    ingested_alerts = []
    monkeypatch.setattr(poller, "ingest", lambda alert, source: ingested_alerts.append((alert, source)) or "inc-poller-1")
    monkeypatch.setattr(poller, "schedule", lambda inc_id, alert: None)

    try:
        await poller.poll_loop()
    except asyncio.CancelledError:
        pass

    assert len(ingested_alerts) == 1
    ingested_alert, source = ingested_alerts[0]
    assert source == "poller"
    assert ingested_alert["labels"]["alertname"] == "ProcessTrue"
    assert ingested_alert["labels"]["savageops"] == "true"

