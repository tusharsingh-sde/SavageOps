"""Grafana webhook listener for SavageOps."""

import logging

from fastapi import APIRouter, BackgroundTasks, Header, HTTPException, Request, status

from app.config import WEBHOOK_SECRET
from app.db import db
from app.graph import run_pipeline

logger = logging.getLogger(__name__)
router = APIRouter(tags=["webhook"])


@router.post("/api/webhook/grafana", status_code=status.HTTP_202_ACCEPTED)
async def grafana_webhook(
    request: Request,
    bg: BackgroundTasks,
    x_webhook_secret: str | None = Header(None, alias="X-Webhook-Secret"),
) -> dict:
    """Receive alerts from Grafana, authenticate via secret header,
    and deduplicate by fingerprint."""
    if not WEBHOOK_SECRET or x_webhook_secret != WEBHOOK_SECRET:
        logger.warning("Rejected webhook request with invalid secret")
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="bad secret")

    payload = await request.json()
    alerts = payload.get("alerts", [])
    created = []

    for alert in alerts:
        if alert.get("status") != "firing":
            continue

        fp = alert.get("fingerprint")
        if not fp:
            logger.warning("Firing alert missing fingerprint: %s", alert)
            continue

        existing = db.table("incidents").select("id").eq("fingerprint", fp).execute().data
        if existing:
            logger.info(
                "Skipping duplicate alert with fingerprint=%s (incident_id=%s)",
                fp,
                existing[0]["id"],
            )
            continue

        insert_res = (
            db.table("incidents")
            .insert(
                {
                    "fingerprint": fp,
                    "status": "received",
                    "alert": alert,
                }
            )
            .execute()
        )

        if insert_res.data:
            incident_id = insert_res.data[0]["id"]
            created.append(incident_id)
            logger.info("Created new incident id=%s fingerprint=%s", incident_id, fp)
            bg.add_task(run_pipeline, incident_id, alert)

    return {"created": created}
