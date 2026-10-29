"""Simulation utility to push synthetic error logs to Loki for testing alerting pipelines."""

import logging
import time
from importlib import resources

import httpx

from savageops.config import LOKI_PUSH_URL

logger = logging.getLogger(__name__)


def push_fake_error(service: str = "dummy-app", n: int = 6):
    """Push synthetic error log records directly to Loki's push API."""
    try:
        line = (resources.files("savageops") / "fixtures" / "sample_logs.txt").read_text(
            encoding="utf-8"
        )
    except Exception:
        line = (
            "2026-10-28 10:00:00 ERROR [dummy-app] ZeroDivisionError: division by zero in app.py:42"
        )

    ts = int(time.time() * 1e9)
    body = {
        "streams": [
            {
                "stream": {"service": service, "level": "error"},
                "values": [[str(ts + (i * 1000)), line] for i in range(n)],
            }
        ]
    }
    push_endpoint = f"{LOKI_PUSH_URL.rstrip('/')}/loki/api/v1/push"
    try:
        r = httpx.post(push_endpoint, json=body, timeout=10.0)
        r.raise_for_status()
    except Exception as e:
        logger.warning("Failed to reach Loki push endpoint %s: %s", push_endpoint, e)
        raise
