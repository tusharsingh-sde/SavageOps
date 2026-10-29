"""FastAPI application entry point for SavageOps with Sentinel, Watchdog, and Lifespan."""

import asyncio
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, HTTPException, Response, status
from langgraph.types import Command
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

from savageops.auth import require_admin
from savageops.config import POLL_ENABLED
from savageops.db import get_incident
from savageops.graph import graph
from savageops.health import router as health_router
from savageops.sentinel.poller import poll_loop
from savageops.sentinel.webhook import router as sentinel_webhook_router
from savageops.watchdog import watchdog_loop


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Manage lifecycle of background Sentinel poller and Watchdog monitor tasks."""
    poller_task = asyncio.create_task(poll_loop()) if POLL_ENABLED else None
    watchdog_task = asyncio.create_task(watchdog_loop())
    yield
    if poller_task:
        poller_task.cancel()
    if watchdog_task:
        watchdog_task.cancel()


app = FastAPI(title="SavageOps", lifespan=lifespan)

app.include_router(health_router)
app.include_router(sentinel_webhook_router)


@app.get("/metrics", tags=["observability"])
def metrics_endpoint():
    """Prometheus metrics scrape target."""
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)


@app.get("/api/incidents/{incident_id}", tags=["incidents"])
def get_incident_endpoint(incident_id: str, admin: dict = Depends(require_admin)):
    """Fetch current incident state, including pending gate decisions (summary or code diff)."""
    inc = get_incident(incident_id)
    if not inc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Incident not found")
    return inc


@app.post("/api/incidents/{incident_id}/respond", tags=["incidents"])
def respond_to_incident(incident_id: str, body: dict, admin: dict = Depends(require_admin)):
    """Submit a decision for Gate 1 (solution approval) or Gate 2 (push approval)."""
    inc = get_incident(incident_id)
    if not inc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Incident not found")

    cfg = {"configurable": {"thread_id": incident_id}}
    graph.invoke(Command(resume=body), cfg)
    return {"ok": True, "incident_id": incident_id}
