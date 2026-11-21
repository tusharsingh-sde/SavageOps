"""Watchdog background monitor for detecting stuck incidents and alerting operators."""

import asyncio
import logging
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx

from savageops.config import SLACK_WEBHOOK_URL
from savageops.db import db
from savageops.metrics import OPEN_INC

logger = logging.getLogger(__name__)

LIMITS = {
    "received": 10,
    "triaged": 10,
    "fixing": 10,
    "pushing": 10,
    "awaiting_user": 30,
    "awaiting_push": 30,
}
NOTIFY_STATUSES = {"awaiting_user", "needs_human", "failed", "pr_open"}
P1_REPEAT_MINUTES = 60


def _parse_utc(ts: Any) -> datetime | None:
    """Parse string or datetime object into UTC-aware datetime."""
    if not ts:
        return None
    if isinstance(ts, datetime):
        if ts.tzinfo is None:
            return ts.replace(tzinfo=UTC)
        return ts.astimezone(UTC)
    try:
        clean_ts = str(ts).replace("Z", "+00:00")
        dt = datetime.fromisoformat(clean_ts)
        if dt.tzinfo is None:
            return dt.replace(tzinfo=UTC)
        return dt.astimezone(UTC)
    except Exception:
        return None


def send_alert(msg: str) -> bool:
    """Log an alert message and forward to Slack webhook if configured."""
    logger.warning("[Watchdog Alert] %s", msg)
    if not SLACK_WEBHOOK_URL:
        return True
    try:
        r = httpx.post(SLACK_WEBHOOK_URL, json={"text": msg}, timeout=5.0)
        r.raise_for_status()
        return True
    except Exception as e:
        logger.error("Watchdog failed to dispatch Slack alert: %s", e)
        return False


def check_stuck():
    """Inspect open incidents across stages and alert if execution exceeds maximum thresholds."""
    now = datetime.now(UTC)
    for status_name, max_mins in LIMITS.items():
        try:
            res = (
                db.table("incidents")
                .select("id,updated_at,status,alert,triage,alerted_at")
                .eq("status", status_name)
                .execute()
            )
            rows = res.data or []
            OPEN_INC.labels(status=status_name).set(len(rows))
            for r in rows:
                status_value = r.get("status", status_name)
                updated_at_val = r.get("updated_at")
                updated_dt = _parse_utc(updated_at_val)
                if updated_dt:
                    try:
                        is_stuck = now - updated_dt > timedelta(minutes=max_mins)
                        alert_payload = r.get("alert") or {}
                        labels = alert_payload.get("labels", {}) if isinstance(alert_payload, dict) else {}
                        triage_payload = r.get("triage") or {}
                        is_p1 = (
                            labels.get("severity", "").upper() == "P1"
                            or (isinstance(triage_payload, dict) and str(triage_payload.get("severity", "")).upper() == "P1")
                        )
                        alerted_at = r.get("alerted_at")
                        repeat_due = False
                        if alerted_at and is_p1:
                            alerted_dt = _parse_utc(alerted_at)
                            if alerted_dt and (now - alerted_dt >= timedelta(minutes=P1_REPEAT_MINUTES)):
                                repeat_due = True
                        should_notify = status_value in NOTIFY_STATUSES and not alerted_at
                        should_notify = should_notify or (is_p1 and repeat_due)
                        should_notify = should_notify or (is_stuck and not alerted_at)
                        if should_notify:
                            sent = send_alert(
                                f"Incident {r.get('id')} is in state '{status_value}'"
                                + (f" for more than {max_mins} minutes." if is_stuck else ".")
                            )
                            if sent:
                                db.table("incidents").update({"alerted_at": now.isoformat()}).eq("id", r.get("id")).execute()
                    except Exception as e:
                        logger.debug("Failed processing stuck row: %s", e)
        except Exception as e:
            logger.debug("Watchdog check error for %s: %s", status_name, e)

    for status_name in NOTIFY_STATUSES - set(LIMITS):
        try:
            rows = (
                db.table("incidents")
                .select("id,updated_at,status,alert,triage,alerted_at")
                .eq("status", status_name)
                .execute()
                .data
                or []
            )
            OPEN_INC.labels(status=status_name).set(len(rows))
            for row in rows:
                alert_payload = row.get("alert") or {}
                labels = alert_payload.get("labels", {}) if isinstance(alert_payload, dict) else {}
                triage_payload = row.get("triage") or {}
                is_p1 = (
                    labels.get("severity", "").upper() == "P1"
                    or (isinstance(triage_payload, dict) and str(triage_payload.get("severity", "")).upper() == "P1")
                )
                alerted_at = row.get("alerted_at")
                repeat_due = False
                if alerted_at and is_p1:
                    alerted_dt = _parse_utc(alerted_at)
                    if alerted_dt and (now - alerted_dt >= timedelta(minutes=P1_REPEAT_MINUTES)):
                        repeat_due = True
                if not alerted_at or (is_p1 and repeat_due):
                    sent = send_alert(f"Incident {row.get('id')} requires attention (state '{status_name}').")
                    if sent:
                        db.table("incidents").update({"alerted_at": now.isoformat()}).eq("id", row.get("id")).execute()
        except Exception as e:
            logger.debug("Watchdog notification error for %s: %s", status_name, e)


async def watchdog_loop():
    """Background loop polling for stalled incidents every 5 minutes."""
    while True:
        try:
            await asyncio.to_thread(check_stuck)
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error("Unexpected error in watchdog loop: %s", e)
        await asyncio.sleep(300)
