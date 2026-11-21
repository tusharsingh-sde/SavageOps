import asyncio
from concurrent.futures import Future
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from fastapi.testclient import TestClient

from savageops.main import app
from savageops.metrics import OPEN_INC
from savageops.sentinel.runner import _discard_future
from savageops.watchdog import LIMITS, _parse_utc, check_stuck


def test_watchdog_pushing_in_limits():
    assert "pushing" in LIMITS
    assert LIMITS["pushing"] == 10


def test_watchdog_parse_utc():
    # Naive ISO string gets UTC tzinfo
    dt = _parse_utc("2026-01-01T00:00:00")
    assert dt is not None
    assert dt.tzinfo == UTC

    # Z-suffixed ISO string
    dt_z = _parse_utc("2026-01-01T00:00:00Z")
    assert dt_z is not None
    assert dt_z.tzinfo == UTC

    # Naive datetime
    naive = datetime(2026, 1, 1, 0, 0, 0)
    dt_naive = _parse_utc(naive)
    assert dt_naive is not None
    assert dt_naive.tzinfo == UTC

    # None / empty / invalid
    assert _parse_utc(None) is None
    assert _parse_utc("") is None
    assert _parse_utc("invalid-ts") is None


def test_watchdog_slack_failure_does_not_update_alerted_at():
    mock_db = MagicMock()
    # Stuck incident in 'pushing' state
    mock_db.table().select().eq().execute.return_value.data = [
        {
            "id": "inc-push-stuck",
            "status": "pushing",
            "updated_at": "2026-01-01T00:00:00",
            "alert": {"labels": {"severity": "warning"}},
            "triage": {},
            "alerted_at": None,
        }
    ]

    with patch("savageops.watchdog.db", mock_db), \
         patch("savageops.watchdog.SLACK_WEBHOOK_URL", "https://hooks.slack.com/services/mock"), \
         patch("httpx.post") as mock_post:
        # Mock 500 error from Slack
        mock_resp = MagicMock()
        mock_resp.raise_for_status.side_effect = httpx.HTTPStatusError("500 Server Error", request=MagicMock(), response=mock_resp)
        mock_post.return_value = mock_resp

        check_stuck()

        # Alert attempted via post
        assert mock_post.called
        # But alerted_at MUST NOT be updated in DB because send_alert failed
        update_calls = [c for c in mock_db.table().update.call_args_list if "alerted_at" in (c.args[0] if c.args else c.kwargs.get("data", {}))]
        assert len(update_calls) == 0


def test_watchdog_p1_triage_severity_repeats_alert_and_updates_open_inc():
    mock_db = MagicMock()
    old_time = (datetime.now(UTC) - timedelta(minutes=90)).isoformat()
    # needs_human incident with P1 severity in triage payload
    mock_db.table().select().eq().execute.return_value.data = [
        {
            "id": "inc-p1-human",
            "status": "needs_human",
            "updated_at": old_time,
            "alert": {"labels": {"severity": "warning"}},
            "triage": {"severity": "P1"},
            "alerted_at": old_time,
        }
    ]

    with patch("savageops.watchdog.db", mock_db), \
         patch("savageops.watchdog.send_alert", return_value=True) as mock_alert:
        check_stuck()
        # Should repeat alert because triage severity is P1 and >60 min elapsed
        assert mock_alert.called
        # Metrics gauge should be updated for needs_human
        assert OPEN_INC.labels(status="needs_human")._value.get() == 1


def test_runner_discard_future_cancelled_no_error():
    f = Future()
    f.cancel()
    # Calling _discard_future on a cancelled future must not raise CancelledError
    _discard_future(f)


@pytest.mark.asyncio
async def test_poller_safe_label_parsing():
    from savageops.sentinel import poller

    mock_client = AsyncMock()
    mock_resp = MagicMock()
    mock_resp.raise_for_status.return_value = None
    # Malformed alerts: non-dict item, labels is None, boolean savageops label
    mock_resp.json.return_value = [
        "not-a-dict",
        {"labels": None},
        {"labels": {"savageops": True}, "annotations": {}},
    ]
    mock_client.get.return_value = mock_resp
    mock_client.__aenter__.return_value = mock_client
    mock_client.__aexit__.return_value = None

    with patch("httpx.AsyncClient", return_value=mock_client), \
         patch("savageops.sentinel.poller.ingest", return_value="inc-poller-1") as mock_ingest, \
         patch("savageops.sentinel.poller.schedule") as mock_sched, \
         patch("asyncio.sleep", side_effect=asyncio.CancelledError):
        try:
            await poller.poll_loop()
        except asyncio.CancelledError:
            pass

        # Only the valid dict with savageops=True should be ingested
        assert mock_ingest.call_count == 1
        assert mock_sched.call_count == 1


def test_webhook_bearer_case_insensitive_and_alerts_validation():
    client = TestClient(app)

    # 1. Lowercase "bearer <token>" should succeed
    res = client.post(
        "/api/webhook/grafana",
        json={"alerts": []},
        headers={"Authorization": "bearer test-webhook-secret"},
    )
    assert res.status_code == 202

    # 2. {"alerts": null} should return 422
    res_null = client.post(
        "/api/webhook/grafana",
        json={"alerts": None},
        headers={"Authorization": "Bearer test-webhook-secret"},
    )
    assert res_null.status_code == 422
    assert "'alerts' must be a list" in res_null.json()["detail"]
