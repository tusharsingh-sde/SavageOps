"""Log retrieval tool querying Loki via Grafana datasource proxy or direct URL with fixture fallback."""

import logging
import os
import re
import time
from datetime import UTC, datetime, timedelta
from importlib import resources
from pathlib import Path
from string import Template
from typing import Any

import httpx

from savageops.config import (
    GRAFANA_TOKEN,
    GRAFANA_URL,
    LOG_SELECTOR,
    LOKI_DATASOURCE_UID,
    LOKI_URL,
)

logger = logging.getLogger(__name__)

SAFE = re.compile(r"^[\w.\-]+$")


def _sanitize_logql_value(v: Any) -> str:
    """Strip quotes, backslashes, braces, backticks, and control characters to prevent LogQL injection."""
    s = str(v)
    return re.sub(r'["\\{}`$\r\n]', "", s)


def _load_fixture_logs() -> str:
    """Load sample logs fixture packaged with savageops."""
    try:
        return (resources.files("savageops") / "fixtures" / "sample_logs.txt").read_text(encoding="utf-8")
    except Exception:
        fallback = Path(__file__).resolve().parents[1] / "fixtures" / "sample_logs.txt"
        if fallback.exists():
            return fallback.read_text(encoding="utf-8")
    return ""


def _ns(dt: datetime) -> int:
    return int(dt.timestamp() * 1e9)


def _parse_timestamp(ts: str | None) -> datetime:
    if not ts:
        return datetime.now(UTC)
    # Trim nanoseconds to microseconds for fromisoformat compatibility
    trimmed = re.sub(r"(\.\d{6})\d+", r"\1", ts)
    clean_ts = trimmed.replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(clean_ts)
        if dt.tzinfo is None:
            return dt.replace(tzinfo=UTC)
        return dt.astimezone(UTC)
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

    raw_labels = alert_dict.get("labels")
    labels = raw_labels if isinstance(raw_labels, dict) else {}
    service = labels.get("service", "dummy-app")

    # Fixture fallback when no Grafana or Loki URL is provided
    if not GRAFANA_URL and not LOKI_URL:
        return _load_fixture_logs()

    if not SAFE.match(service):
        raise ValueError(f"Invalid service label for LogQL: {service}")

    # Build safe query with sanitized label values
    safe_labels = {
        k: _sanitize_logql_value(v)
        for k, v in labels.items()
        if isinstance(k, str)
    }
    safe_labels["service"] = _sanitize_logql_value(service)

    try:
        query = Template(LOG_SELECTOR).safe_substitute(safe_labels)
    except Exception:
        query = f'{{service="{safe_labels["service"]}"}} |= "Error"'
    # Always query the complete stream. Loki emits each traceback frame as a separate
    # entry, so a line-level |= filter would discard the frames needed for triage.
    query = query.split("|", 1)[0].strip()

    start_dt = _parse_timestamp(alert_dict.get("startsAt")) - timedelta(minutes=minutes_before)
    start_ns = _ns(start_dt)
    end_ns = max(int(time.time() * 1e9), start_ns + 60_000_000_000)

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
                "limit": 500,
                "direction": "forward",
            },
            timeout=15.0,
        )
        resp.raise_for_status()
        raw_parsed = resp.json()
        raw_json = raw_parsed if isinstance(raw_parsed, dict) else {}
        data = raw_json.get("data") if isinstance(raw_json, dict) else {}
        result = data.get("result", []) if isinstance(data, dict) else []
        rows = sorted(
            (
                v
                for s in result
                if isinstance(s, dict)
                for v in s.get("values", [])
                if isinstance(v, (list, tuple)) and len(v) >= 2
            ),
            key=lambda v: int(v[0]),
        )
        if not rows:
            return ""
        # Select a compact window around the first error event while keeping unfiltered
        # stack-frame entries that share its timestamp neighborhood.
        error_rows = [v for v in rows if re.search(r"(?i)error|exception|traceback|fatal", v[1])]
        if error_rows:
            center = int(error_rows[0][0])
            margin_ns = 90 * 1_000_000_000
            rows = [v for v in rows if abs(int(v[0]) - center) <= margin_ns]
        return "\n".join(v[1] for v in rows)
    except Exception as e:
        logger.warning("Failed to query Loki at %s: %s", base, e)
        if os.getenv("SAVAGEOPS_MOCK") == "1":
            return _load_fixture_logs()
        raise RuntimeError(f"Failed to query Loki at {base}: {e}") from e
