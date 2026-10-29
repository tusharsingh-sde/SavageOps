"""Sentinel Ingestion Engine: deduplication, debouncing, and normalization."""

import logging
import time
from datetime import UTC, datetime

from savageops.config import GROUP_COOLDOWN_SECONDS
from savageops.db import db
from savageops.security import redact_untrusted_data

logger = logging.getLogger(__name__)

OPEN = [
    "received", "triaged", "awaiting_user", "awaiting_push", "fixing", "pushing",
    "needs_human", "failed", "pr_open",
]


def normalize(a: dict) -> dict:
    """Normalize alert dictionary from webhook or poller into a standard shape."""
    starts_at = a.get("startsAt")
    if starts_at:
        try:
            parsed = datetime.fromisoformat(str(starts_at).replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=UTC)
            starts_at = parsed.astimezone(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
        except ValueError:
            pass
    return redact_untrusted_data({
        "status": "firing",
        "labels": a.get("labels", {}),
        "annotations": a.get("annotations", {}),
        "startsAt": starts_at,
        "fingerprint": a.get("fingerprint"),
        "generatorURL": a.get("generatorURL"),
    })


def _record_metric(source: str, result: str):
    if source == "canary":
        return
    try:
        from savageops.metrics import INGEST

        INGEST.labels(source=source, result=result).inc()
    except Exception:
        pass


def ingest(alert: dict, source: str) -> str | None:
    """Process an alert: dedupe open groups, cool down resolved groups, and persist if new.

    Returns incident_id if a new incident was created, else None.
    """
    fp = alert.get("fingerprint") or "unknown"
    starts_at = alert.get("startsAt") or "unknown"
    key = f"{fp}:{starts_at}"

    labels = alert.get("labels", {})
    group = f"{labels.get('alertname', 'unknown')}:{labels.get('service', 'unknown')}"
    if source == "canary":
        group = f"canary:{group}"

    # 1. Exact Duplicate Guard
    try:
        existing = db.table("incidents").select("id").eq("fingerprint", key).execute().data
        if existing:
            _record_metric(source, "duplicate")
            logger.info("Sentinel: Dropped duplicate alert %s (source=%s)", key, source)
            return None
    except Exception as e:
        logger.debug("Sentinel: could not check duplicate by fingerprint: %s", e)

    # 2. Debounce Flapping Guard
    recent = []
    try:
        recent = (
            db.table("incidents")
            .select("id,occurrences")
            .eq("group_key", group)
            .in_("status", OPEN)
            .execute()
            .data
        )
    except Exception as e:
        logger.debug("Sentinel: group_key or occurrences not queryable (migration pending?): %s", e)

    if recent:
        # Open incidents remain the deduplication target beyond the debounce interval.
        # The database partial unique index makes this safe across multiple workers.
        r = recent[0]
        current_occurrences = r.get("occurrences", 1) or 1
        now_iso = datetime.now(UTC).isoformat()
        try:
            db.table("incidents").update(
                {
                    "occurrences": current_occurrences + 1,
                    "last_seen_at": now_iso,
                }
            ).eq("id", r["id"]).execute()
        except Exception:
            pass
        _record_metric(source, "debounced")
        logger.info(
            "Sentinel: Debounced flapping alert %s into incident %s (occurrences=%d)",
            group,
            r["id"],
            current_occurrences + 1,
        )
        return None

    cooldown_since = datetime.fromtimestamp(time.time() - GROUP_COOLDOWN_SECONDS, UTC).isoformat()
    try:
        recently_seen = (
            db.table("incidents")
            .select("id,status")
            .eq("group_key", group)
            .gte("last_seen_at", cooldown_since)
            .execute()
            .data
            or []
        )
        if any(r.get("status") not in OPEN for r in recently_seen):
            _record_metric(source, "cooldown")
            return None
    except Exception as e:
        logger.debug("Sentinel: group cooldown lookup unavailable: %s", e)

    # 3. Create New Incident
    now_iso = datetime.now(UTC).isoformat()
    row_payload = {
        "fingerprint": key,
        "group_key": group,
        "source": source,
        "alert": redact_untrusted_data(alert),
        "status": "canary" if source == "canary" else "received",
        "occurrences": 1,
        "last_seen_at": now_iso,
        "created_at": now_iso,
        "updated_at": now_iso,
    }
    try:
        res = db.table("incidents").insert(row_payload).execute()
        row = res.data[0]
        _record_metric(source, "new")
        logger.info("Sentinel: Ingested new incident %s (key=%s)", row["id"], key)
        return row["id"]
    except Exception as e:
        err_msg = str(e)
        if "does not exist" in err_msg or "PGRST204" in err_msg:
            fallback_payload = {
                "fingerprint": key,
                "source": source,
                "alert": redact_untrusted_data(alert),
                "status": "canary" if source == "canary" else "received",
                "pending": {"group_key": group, "source": source, "occurrences": 1},
                "created_at": now_iso,
                "updated_at": now_iso,
            }
            try:
                res = db.table("incidents").insert(fallback_payload).execute()
                row = res.data[0]
                _record_metric(source, "new")
                logger.info(
                    "Sentinel: Ingested new incident via fallback %s (key=%s)", row["id"], key
                )
                return row["id"]
            except Exception as e2:
                logger.warning(
                    "Sentinel: Insert collision or error in fallback for key %s: %s", key, e2
                )
                return None
        logger.warning("Sentinel: Insert collision or error for key %s: %s", key, e)
        return None


def monitoring_down():
    """Create a high-priority incident when Grafana monitoring is unreachable."""
    key = f"monitoring-down:{int(time.time() // 3600)}"
    existing = []
    try:
        existing = db.table("incidents").select("id").eq("fingerprint", key).execute().data
    except Exception:
        pass
    if existing:
        return

    now_iso = datetime.now(UTC).isoformat()
    payload = {
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
    try:
        db.table("incidents").insert(payload).execute()
        logger.error("Sentinel: Created monitoring-down incident due to 3 consecutive failures")
    except Exception as e:
        err_msg = str(e)
        if "does not exist" in err_msg or "PGRST204" in err_msg:
            fallback_payload = {
                "fingerprint": key,
                "alert": {"labels": {"alertname": "GrafanaUnreachable"}},
                "status": "needs_human",
                "pending": {
                    "error": "Grafana lagataar 3 baar unreachable raha",
                    "group_key": "monitoring-down",
                },
                "created_at": now_iso,
                "updated_at": now_iso,
            }
            try:
                db.table("incidents").insert(fallback_payload).execute()
                logger.error("Sentinel: Created monitoring-down incident via fallback")
            except Exception:
                pass
        else:
            logger.warning("Sentinel: Failed to create monitoring-down incident: %s", e)
