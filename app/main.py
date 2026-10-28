"""FastAPI application entry point for SavageOps."""

from fastapi import Depends, FastAPI, HTTPException, Response, status
from langgraph.types import Command
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Histogram, generate_latest

from app.auth import require_admin
from app.db import get_incident
from app.graph import graph
from app.health import router as health_router
from app.webhook import router as incident_router

app = FastAPI(title="SavageOps")
app.include_router(health_router)
app.include_router(incident_router)

# Prometheus Metrics
AGENT_RUNS = Counter("agent_runs_total", "Total agent executions", ["agent", "kind"])
AGENT_LATENCY = Histogram("agent_latency_seconds", "Agent latency in seconds", ["agent"])
ESCALATIONS = Counter("incident_escalations_total", "Total incidents escalated to needs_human")


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
