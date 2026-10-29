"""Grafana Alerting Webhook Receiver for Sentinel."""

from fastapi import APIRouter, BackgroundTasks, Body, Header, HTTPException, status

from savageops.config import WEBHOOK_SECRET
from savageops.sentinel.ingest import ingest, normalize
from savageops.sentinel.runner import start

router = APIRouter()


@router.post("/api/webhook/grafana", status_code=status.HTTP_202_ACCEPTED, tags=["sentinel"])
def grafana_webhook(
    bg: BackgroundTasks,
    payload: dict = Body(...),
    authorization: str | None = Header(None),
):
    """Receive alert notifications from Grafana, authenticate via Bearer token, and dispatch to Sentinel."""
    if authorization != f"Bearer {WEBHOOK_SECRET}":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing webhook authorization secret.",
        )

    created = []
    for a in payload.get("alerts", []):
        if a.get("status") != "firing":
            continue
        alert = normalize(a)
        inc_id = ingest(alert, "webhook")
        if inc_id:
            created.append(inc_id)
            bg.add_task(start, inc_id, alert)

    return {"created": created}
