"""Log retrieval tool querying Loki via Grafana datasource proxy or direct URL with fixture fallback."""

import logging
import re
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from string import Template

import httpx

from app.config import (
    GRAFANA_TOKEN,
    GRAFANA_URL,
    LOG_SELECTOR,
    LOKI_DATASOURCE_UID,
    LOKI_URL,
)

logger = logging.getLogger(__name__)

SAFE = re.compile(r"^[\w.\-]+$")


def _ns(dt: datetime) -> int:
    return int(dt.timestamp() * 1e9)


def _parse_timestamp(ts: str | None) -> datetime:
    if not ts:
        return datetime.now(UTC)
    # Trim nanoseconds to microseconds for fromisoformat compatibility
    trimmed = re.sub(r"(\.\d{6})\d+", r"\1", ts)
    clean_ts = trimmed.replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(clean_ts)
    except ValueError:
        return datetime.now(UTC)


def get_logs(alert: dict | str, minutes_before: int = 5) -> str:
    """Retrieve error logs around the alert episode.

    Accepts either an alert dict (with labels and startsAt) or a service name string for backwards compatibility.
    """
    # Backwards compatibility: if alert is passed as a string service name
    if isinstance(alert, str):
        alert_dict = {
            "labels": {"service": alert},
            "startsAt": datetime.now(UTC).isoformat(),
        }
    else:
        alert_dict = alert

    labels = alert_dict.get("labels", {})
    service = labels.get("service", "dummy-app")

    # Fixture fallback when no Grafana or Loki URL is provided
    if not GRAFANA_URL and not LOKI_URL:
        fixture_paths = [
            Path(__file__).resolve().parents[2] / "fixtures" / "sample_logs.txt",
            Path(__file__).resolve().parents[1] / "fixtures" / "sample_logs.txt",
        ]
        for fp in fixture_paths:
            if fp.exists():
                return fp.read_text(encoding="utf-8")
        return ""

    if not SAFE.match(service):
        raise ValueError(f"Invalid service label for LogQL: {service}")

    # Build safe query
    try:
        query = Template(LOG_SELECTOR).safe_substitute(labels)
    except Exception:
        query = f'{{service="{service}"}} |= "Error"'

    start_dt = _parse_timestamp(alert_dict.get("startsAt")) - timedelta(minutes=minutes_before)
    end_ns = int(time.time() * 1e9)
    start_ns = _ns(start_dt)

    base = LOKI_URL or f"{GRAFANA_URL.rstrip('/')}/api/datasources/proxy/uid/{LOKI_DATASOURCE_UID}"
    headers = {"Authorization": f"Bearer {GRAFANA_TOKEN}"} if GRAFANA_TOKEN and not LOKI_URL else {}

    try:
        resp = httpx.get(
            f"{base.rstrip('/')}/loki/api/v1/query_range",
            headers=headers,
            params={
                "query": query,
                "start": start_ns,
                "end": end_ns,
                "limit": 200,
                "direction": "forward",
            },
            timeout=15.0,
        )
        resp.raise_for_status()
        data = resp.json().get("data", {}).get("result", [])
        rows = sorted(
            (v for s in data for v in s.get("values", [])),
            key=lambda v: int(v[0]),
        )
        return "\n".join(v[1] for v in rows)
    except Exception as e:
        logger.warning("Failed to query Loki at %s: %s. Falling back to local fixture.", base, e)
        fixture_paths = [
            Path(__file__).resolve().parents[2] / "fixtures" / "sample_logs.txt",
            Path(__file__).resolve().parents[1] / "fixtures" / "sample_logs.txt",
        ]
        for fp in fixture_paths:
            if fp.exists():
                return fp.read_text(encoding="utf-8")
        return ""
