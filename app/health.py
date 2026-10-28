"""Health check endpoints for SavageOps."""

import httpx
from fastapi import APIRouter

from app.config import LOKI_URL
from app.db import db
from app.tools.github_tools import _get_client_and_repo

router = APIRouter(tags=["health"])


@router.get("/health")
def health() -> dict[str, str]:
    """Lightweight liveness probe."""
    return {"status": "ok"}


@router.get("/health/deep")
def deep_health() -> dict:
    """Deep dependency health check for Supabase, GitHub, and Loki."""
    checks = {}

    # Check Supabase
    try:
        db.table("incidents").select("id").limit(1).execute()
        checks["supabase"] = "ok"
    except Exception as e:
        checks["supabase"] = f"fail: {e}"

    # Check GitHub repo access
    try:
        _, repo = _get_client_and_repo()
        _ = repo.default_branch
        checks["github"] = "ok"
    except Exception as e:
        checks["github"] = f"fail: {e}"

    # Check Loki / Fixture mode
    if LOKI_URL:
        try:
            httpx.get(f"{LOKI_URL}/ready", timeout=5.0).raise_for_status()
            checks["loki"] = "ok"
        except Exception as e:
            checks["loki"] = f"fail: {e}"
    else:
        checks["loki"] = "fixture_mode"

    all_ok = all(not str(val).startswith("fail") for val in checks.values())
    return {"ok": all_ok, "checks": checks}
