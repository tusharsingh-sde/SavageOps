"""Backup active poller for Sentinel to query Grafana Alertmanager periodically."""

import asyncio
import logging
import time
from typing import Any

import httpx

from savageops.config import GRAFANA_TOKEN, GRAFANA_URL, POLL_INTERVAL_SECONDS
from savageops.sentinel.ingest import ingest, monitoring_down, normalize
from savageops.sentinel.runner import schedule

logger = logging.getLogger(__name__)

state: dict[str, Any] = {"last_ok": None, "fails": 0, "down_notified": False}


async def poll_loop():
    """Periodically poll Grafana Alertmanager active alerts as a fallback safety net."""
    state["started_at"] = time.monotonic()
    headers = {"Authorization": f"Bearer {GRAFANA_TOKEN}"} if GRAFANA_TOKEN else {}
    async with httpx.AsyncClient(timeout=10) as client:
        while True:
            try:
                url = f"{GRAFANA_URL.rstrip('/')}/api/alertmanager/grafana/api/v2/alerts"
                r = await client.get(
                    url,
                    params={"active": "true", "silenced": "false", "inhibited": "false"},
                    headers=headers,
                )
                r.raise_for_status()
                alerts = r.json()
                if isinstance(alerts, list):
                    for a in alerts:
                        if a.get("labels", {}).get("savageops", "").lower() != "true":
                            continue
                        alert = normalize(a)
                        inc_id = await asyncio.to_thread(ingest, alert, "poller")
                        if inc_id:
                            schedule(inc_id, alert)
                state["last_ok"] = time.time()
                state["fails"] = 0
                state["down_notified"] = False
            except asyncio.CancelledError:
                break
            except Exception as e:
                state["fails"] += 1
                logger.warning(
                    "Sentinel Poller: Error connecting to Grafana (fail_count=%d): %s",
                    state["fails"],
                    e,
                )
                if (
                    state["fails"] >= 3
                    and not state["down_notified"]
                    and time.monotonic() - state["started_at"] >= 120
                ):
                    await asyncio.to_thread(monitoring_down)
                    state["down_notified"] = True

            await asyncio.sleep(POLL_INTERVAL_SECONDS)
