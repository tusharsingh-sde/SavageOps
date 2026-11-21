"""Grafana Alerting Webhook Receiver for Sentinel."""

import hmac

from fastapi import APIRouter, BackgroundTasks, Body, Header, HTTPException, status

from savageops.config import WEBHOOK_SECRET
from savageops.sentinel.ingest import ingest, normalize
from savageops.sentinel.runner import schedule

router = APIRouter()


@router.post("/api/webhook/grafana", status_code=status.HTTP_202_ACCEPTED, tags=["sentinel"])
def grafana_webhook(
    bg: BackgroundTasks,
    payload: dict = Body(...),
    authorization: str | None = Header(None),
):
    """Receive alert notifications from Grafana, authenticate via Bearer token, and dispatch to Sentinel."""
    if not WEBHOOK_SECRET or not authorization:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing webhook authorization secret.",
        )
    parts = authorization.strip().split(None, 1)
    if len(parts) != 2 or parts[0].lower() != "bearer":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid authorization header format.",
        )
    token = parts[1].strip().strip('"')
    if not hmac.compare_digest(token, WEBHOOK_SECRET):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing webhook authorization secret.",
        )

    alerts = payload.get("alerts")
    if alerts is None or not isinstance(alerts, list):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="'alerts' must be a list",
        )

    created = []
    for a in alerts:
        if not isinstance(a, dict):
            continue
        if a.get("status") != "firing":
            continue
        alert = normalize(a)
        inc_id = ingest(alert, "webhook")
        if inc_id:
            created.append(inc_id)
            bg.add_task(schedule, inc_id, alert)

    return {"created": created}
