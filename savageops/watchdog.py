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
            res = db.table("incidents").select("id,updated_at").eq("status", status_name).execute()
            rows = res.data or []
            OPEN_INC.labels(status=status_name).set(len(rows))
            for r in rows:
                updated_at_val = r.get("updated_at")
                if updated_at_val:
                    try:
                        clean_ts = str(updated_at_val).replace("Z", "+00:00")
                        updated_dt = datetime.fromisoformat(clean_ts)
                        if now - updated_dt > timedelta(minutes=max_mins):
                            send_alert(
                                f"Incident {r.get('id')} has been stuck in state '{status_name}' "
                                f"for more than {max_mins} minutes."
                            )
                    except Exception:
                        pass
        except Exception as e:
            logger.debug("Watchdog check error for %s: %s", status_name, e)


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
