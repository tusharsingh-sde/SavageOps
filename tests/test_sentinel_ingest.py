"""Tests for Sentinel alert ingestion, deduplication, normalization, and debouncing."""

from datetime import datetime

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
