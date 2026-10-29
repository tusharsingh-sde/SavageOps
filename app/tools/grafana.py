"""Grafana integration tools for querying alerts and checking active state."""

import logging

import httpx

from app.config import GRAFANA_TOKEN, GRAFANA_URL

logger = logging.getLogger(__name__)


def get_alert(fingerprint: str) -> dict | None:
    """Fetch an active alert matching the fingerprint from Grafana Alertmanager."""
    if not GRAFANA_URL:
        return None
    headers = {"Authorization": f"Bearer {GRAFANA_TOKEN}"} if GRAFANA_TOKEN else {}
    url = f"{GRAFANA_URL.rstrip('/')}/api/alertmanager/grafana/api/v2/alerts"
    try:
        r = httpx.get(
            url,
            params={"active": "true", "silenced": "false", "inhibited": "false"},
            headers=headers,
            timeout=10,
        )
        r.raise_for_status()
        alerts = r.json()
        if isinstance(alerts, list):
            return next((a for a in alerts if a.get("fingerprint") == fingerprint), None)
    except Exception as e:
        logger.debug("Failed to query Grafana alerts: %s", e)
    return None


def still_firing(fingerprint: str) -> bool:
    """Check if an alert is still firing; return True if Grafana cannot be contacted."""
    if not GRAFANA_URL:
        return True
    try:
        alert = get_alert(fingerprint)
        return alert is not None
    except Exception:
        return True
