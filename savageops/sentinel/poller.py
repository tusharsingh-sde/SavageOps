"""Backup active poller for Sentinel to query Grafana Alertmanager periodically."""

import asyncio
import logging
import time
from typing import Any

import httpx

from savageops.config import GRAFANA_TOKEN, GRAFANA_URL, POLL_INTERVAL_SECONDS
from savageops.sentinel.ingest import ingest, monitoring_down, normalize
from savageops.sentinel.runner import start

logger = logging.getLogger(__name__)

state: dict[str, Any] = {"last_ok": None, "fails": 0}


async def poll_loop():
    """Periodically poll Grafana Alertmanager active alerts as a fallback safety net."""
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
                        alert = normalize(a)
                        inc_id = await asyncio.to_thread(ingest, alert, "poller")
                        if inc_id:
                            asyncio.create_task(asyncio.to_thread(start, inc_id, alert))
                state["last_ok"] = time.time()
                state["fails"] = 0
            except asyncio.CancelledError:
                break
            except Exception as e:
                state["fails"] += 1
                logger.warning(
                    "Sentinel Poller: Error connecting to Grafana (fail_count=%d): %s",
                    state["fails"],
                    e,
                )
                if state["fails"] == 3:
                    await asyncio.to_thread(monitoring_down)

            await asyncio.sleep(POLL_INTERVAL_SECONDS)
