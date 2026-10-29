"""Sentinel Ingestion Engine: deduplication, debouncing, and normalization."""

import logging
import time
from datetime import UTC, datetime, timedelta

from app.config import DEBOUNCE_MINUTES
from app.db import db

logger = logging.getLogger(__name__)

OPEN = ["received", "triaged", "awaiting_user", "awaiting_push", "fixing"]


def normalize(a: dict) -> dict:
    """Normalize alert dictionary from webhook or poller into a standard shape."""
    return {
        "status": "firing",
        "labels": a.get("labels", {}),
        "annotations": a.get("annotations", {}),
        "startsAt": a.get("startsAt"),
        "fingerprint": a.get("fingerprint"),
        "generatorURL": a.get("generatorURL"),
    }


def _record_metric(source: str, result: str):
    try:
        from app.metrics import INGEST

        INGEST.labels(source=source, result=result).inc()
    except Exception:
        pass


def ingest(alert: dict, source: str) -> str | None:
    """Process an alert: dedupe, debounce flapping episodes, and persist if new.

    Returns incident_id if a new incident was created, else None.
    """
    fp = alert.get("fingerprint") or "unknown"
    starts_at = alert.get("startsAt") or "unknown"
    key = f"{fp}:{starts_at}"

    labels = alert.get("labels", {})
    group = f"{labels.get('alertname', 'unknown')}:{labels.get('service', 'unknown')}"

    # 1. Exact Duplicate Guard
    existing = db.table("incidents").select("id").eq("fingerprint", key).execute().data
    if existing:
        _record_metric(source, "duplicate")
        logger.info("Sentinel: Dropped duplicate alert %s (source=%s)", key, source)
        return None

    # 2. Debounce Flapping Guard
    since = (datetime.now(UTC) - timedelta(minutes=DEBOUNCE_MINUTES)).isoformat()
    recent = (
        db.table("incidents")
        .select("id,occurrences")
        .eq("group_key", group)
        .in_("status", OPEN)
        .gte("last_seen_at", since)
        .execute()
        .data
    )
    if recent:
        r = recent[0]
        current_occurrences = r.get("occurrences", 1) or 1
        now_iso = datetime.now(UTC).isoformat()
        db.table("incidents").update(
            {
                "occurrences": current_occurrences + 1,
                "last_seen_at": now_iso,
            }
        ).eq("id", r["id"]).execute()
        _record_metric(source, "debounced")
        logger.info(
            "Sentinel: Debounced flapping alert %s into incident %s (occurrences=%d)",
            group,
            r["id"],
            current_occurrences + 1,
        )
        return None

    # 3. Create New Incident
    try:
        now_iso = datetime.now(UTC).isoformat()
        row_payload = {
            "fingerprint": key,
            "group_key": group,
            "source": source,
            "alert": alert,
            "status": "received",
            "occurrences": 1,
            "last_seen_at": now_iso,
            "created_at": now_iso,
            "updated_at": now_iso,
        }
        res = db.table("incidents").insert(row_payload).execute()
        row = res.data[0]
        _record_metric(source, "new")
        logger.info("Sentinel: Ingested new incident %s (key=%s)", row["id"], key)
        return row["id"]
    except Exception as e:
        logger.warning("Sentinel: Insert collision or error for key %s: %s", key, e)
        return None


def monitoring_down():
    """Create a high-priority incident when Grafana monitoring is unreachable."""
    key = f"monitoring-down:{int(time.time() // 3600)}"
    existing = db.table("incidents").select("id").eq("fingerprint", key).execute().data
    if existing:
        return
    try:
        now_iso = datetime.now(UTC).isoformat()
        db.table("incidents").insert(
            {
                "fingerprint": key,
                "group_key": "monitoring-down",
                "source": "poller",
                "alert": {"labels": {"alertname": "GrafanaUnreachable"}},
                "status": "needs_human",
                "pending": {"error": "Grafana lagataar 3 baar unreachable raha"},
                "last_seen_at": now_iso,
                "created_at": now_iso,
                "updated_at": now_iso,
            }
        ).execute()
        logger.error("Sentinel: Created monitoring-down incident due to 3 consecutive failures")
    except Exception as e:
        logger.warning("Sentinel: Failed to create monitoring-down incident: %s", e)
