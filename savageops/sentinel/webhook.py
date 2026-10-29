"""Grafana Alerting Webhook Receiver for Sentinel."""

import hmac

from fastapi import APIRouter, BackgroundTasks, Body, Header, HTTPException, status

from savageops.config import WEBHOOK_SECRET
from savageops.security import redact_untrusted_data
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
    expected = f"Bearer {WEBHOOK_SECRET}" if WEBHOOK_SECRET else ""
    auth_clean = authorization.strip().replace('"', "") if authorization else ""
    exp_clean = expected.strip().replace('"', "") if expected else ""
    if not WEBHOOK_SECRET or not authorization or (
        not hmac.compare_digest(authorization, expected) and not hmac.compare_digest(auth_clean, exp_clean)
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing webhook authorization secret.",
        )

    created = []
    for a in payload.get("alerts", []):
        if a.get("status") != "firing":
            continue
        alert = normalize(a)
        alert = redact_untrusted_data(alert)
        inc_id = ingest(alert, "webhook")
        if inc_id:
            created.append(inc_id)
            bg.add_task(schedule, inc_id, alert)

    return {"created": created}
