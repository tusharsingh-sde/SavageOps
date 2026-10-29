"""Watchdog background monitor for detecting stuck incidents and alerting operators."""

import asyncio
import logging
from datetime import UTC, datetime, timedelta

import httpx

from savageops.config import SLACK_WEBHOOK_URL
from savageops.db import db
from savageops.metrics import OPEN_INC

logger = logging.getLogger(__name__)

LIMITS = {
    "received": 10,
    "triaged": 10,
    "fixing": 10,
    "awaiting_user": 30,
    "awaiting_push": 30,
}
NOTIFY_STATUSES = {"awaiting_user", "needs_human", "failed", "pr_open"}
P1_REPEAT_MINUTES = 60


def send_alert(msg: str):
    """Log an alert message and forward to Slack webhook if configured."""
    logger.warning("[Watchdog Alert] %s", msg)
    if SLACK_WEBHOOK_URL:
        try:
            httpx.post(SLACK_WEBHOOK_URL, json={"text": msg}, timeout=5.0)
        except Exception as e:
            logger.error("Watchdog failed to dispatch Slack alert: %s", e)


def check_stuck():
    """Inspect open incidents across stages and alert if execution exceeds maximum thresholds."""
    now = datetime.now(UTC)
    for status_name, max_mins in LIMITS.items():
        try:
            res = db.table("incidents").select("id,updated_at,status,alert,alerted_at").eq("status", status_name).execute()
            rows = res.data or []
            OPEN_INC.labels(status=status_name).set(len(rows))
            for r in rows:
                status_value = r.get("status", status_name)
                updated_at_val = r.get("updated_at")
                if updated_at_val:
                    try:
                        clean_ts = str(updated_at_val).replace("Z", "+00:00")
                        updated_dt = datetime.fromisoformat(clean_ts)
                        is_stuck = now - updated_dt > timedelta(minutes=max_mins)
                        alert_payload = r.get("alert") or {}
                        labels = alert_payload.get("labels", {}) if isinstance(alert_payload, dict) else {}
                        is_p1 = labels.get("severity", "").upper() == "P1"
                        alerted_at = r.get("alerted_at")
                        repeat_due = False
                        if alerted_at and is_p1:
                            repeat_due = now - datetime.fromisoformat(str(alerted_at).replace("Z", "+00:00")) >= timedelta(minutes=P1_REPEAT_MINUTES)
                        should_notify = status_value in NOTIFY_STATUSES and not alerted_at
                        should_notify = should_notify or (is_p1 and repeat_due)
                        should_notify = should_notify or (is_stuck and not alerted_at)
                        if should_notify:
                            send_alert(
                                f"Incident {r.get('id')} is in state '{status_value}'"
                                + (f" for more than {max_mins} minutes." if is_stuck else ".")
                            )
                            db.table("incidents").update({"alerted_at": now.isoformat()}).eq("id", r.get("id")).execute()
                    except Exception:
                        pass
        except Exception as e:
            logger.debug("Watchdog check error for %s: %s", status_name, e)

    for status_name in NOTIFY_STATUSES - set(LIMITS):
        try:
            rows = db.table("incidents").select("id,alert,alerted_at").eq("status", status_name).execute().data or []
            for row in rows:
                if not row.get("alerted_at"):
                    send_alert(f"Incident {row.get('id')} requires attention (state '{status_name}').")
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
