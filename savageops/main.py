"""FastAPI application entry point for SavageOps with durable background processing."""

import asyncio
import hmac
from contextlib import asynccontextmanager
from threading import Lock

from fastapi import BackgroundTasks, Depends, FastAPI, Header, HTTPException, Response, status
from langgraph.types import Command
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

from savageops.auth import require_admin
from savageops.config import (
    DATABASE_URL,
    METRICS_TOKEN,
    POLL_ENABLED,
    WEBHOOK_SECRET,
    validate_startup_config,
)
from savageops.db import db, get_incident
from savageops.health import router as health_router
from savageops.sentinel.poller import poll_loop
from savageops.sentinel.webhook import router as sentinel_webhook_router
from savageops.watchdog import watchdog_loop

_incident_locks: dict[str, Lock] = {}
_background_tasks: set[asyncio.Task] = set()


def _track_task(task: asyncio.Task) -> None:
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)


def _start_incident(incident_id: str, alert: dict) -> None:
    from savageops.sentinel.runner import schedule

    schedule(incident_id, alert)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Validate required credentials and start owned background loops."""
    validate_startup_config()
    if not WEBHOOK_SECRET:
        raise RuntimeError("WEBHOOK_SECRET must be configured before SavageOps starts")

    checkpointer_context = None
    if DATABASE_URL:
        from langgraph.checkpoint.postgres import PostgresSaver

        checkpointer_context = PostgresSaver.from_conn_string(DATABASE_URL)
        checkpointer = checkpointer_context.__enter__()
        checkpointer.setup()
        from savageops.graph import configure_postgres_checkpointer

        configure_postgres_checkpointer(checkpointer)

    poller_task = asyncio.create_task(poll_loop()) if POLL_ENABLED else None
    watchdog_task = asyncio.create_task(watchdog_loop())
    try:
        rows = (
            db.table("incidents")
            .select("id,alert")
            .in_("status", ["received", "triaged"])
            .execute()
            .data
            or []
        )
        for row in rows:
            if row.get("id") and row.get("alert"):
                _track_task(asyncio.create_task(asyncio.to_thread(_start_incident, row["id"], row["alert"])))
    except Exception:
        import logging

        logging.getLogger(__name__).exception("Could not recover pending incidents on startup")
    yield
    tasks = [task for task in (poller_task, watchdog_task) if task]
    for task in tasks:
        task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)
    if checkpointer_context:
        checkpointer_context.__exit__(None, None, None)


app = FastAPI(title="SavageOps", lifespan=lifespan)
app.include_router(health_router)
app.include_router(sentinel_webhook_router)


@app.get("/metrics", tags=["observability"])
def metrics_endpoint(authorization: str | None = Header(None)):
    """Token-protected Prometheus scrape target, independent of interactive admin JWTs."""
    if not METRICS_TOKEN:
        raise HTTPException(status_code=503, detail="METRICS_TOKEN is not configured")
    expected = f"Bearer {METRICS_TOKEN}"
    if not authorization or not hmac.compare_digest(authorization, expected):
        raise HTTPException(status_code=401, detail="Invalid or missing metrics token")
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)


@app.get("/api/incidents/{incident_id}", tags=["incidents"])
def get_incident_endpoint(incident_id: str, admin: dict = Depends(require_admin)):
    """Fetch current incident state, including pending gate decisions."""
    inc = get_incident(incident_id)
    if not inc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Incident not found")
    return inc


@app.post("/api/incidents/{incident_id}/respond", tags=["incidents"])
def respond_to_incident(
    incident_id: str,
    body: dict,
    bg: BackgroundTasks,
    admin: dict = Depends(require_admin),
):
    """Validate the pending human gate, then resume its graph in a background task."""
    lock = _incident_locks.setdefault(incident_id, Lock())
    with lock:
        inc = get_incident(incident_id)
        if not inc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Incident not found")
        gate = (inc.get("pending") or {}).get("gate")
        expected_status = "awaiting_user" if gate == 1 else "awaiting_push" if gate == 2 else None
        if inc.get("status") != expected_status:
            raise HTTPException(status_code=409, detail="This gate is no longer awaiting a response")
        action = body.get("action")
        allowed = {1: {"approve", "reject"}, 2: {"push", "revise", "reject"}}
        if gate not in allowed or action not in allowed[gate]:
            raise HTTPException(status_code=409, detail="Response does not match the pending gate")
        if gate == 1 and action == "approve" and body.get("choice", "permanent") not in {"fast", "permanent"}:
            raise HTTPException(status_code=422, detail="choice must be 'fast' or 'permanent'")
        if gate == 1:
            next_status = "fixing" if action == "approve" else "triaged"
        else:
            next_status = "pushing" if action == "push" else "fixing" if action == "revise" else "rejected"
        db.table("incidents").update({"status": next_status, "alerted_at": None}).eq("id", incident_id).execute()
        bg.add_task(_resume_incident, incident_id, body)
    return {"ok": True, "incident_id": incident_id}


def _resume_incident(incident_id: str, body: dict) -> None:
    from savageops.graph import graph

    try:
        graph.invoke(Command(resume=body), {"configurable": {"thread_id": incident_id}})
    except Exception as e:
        from savageops.db import update_incident

        update_incident(incident_id, status="failed", pending={"error": str(e)[:500]})
