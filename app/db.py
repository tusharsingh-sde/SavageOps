"""Database operations wrapper for Supabase with in-memory fallback when unconfigured."""

import logging
from typing import Any

from supabase import Client, create_client

from app.config import SUPABASE_SERVICE_KEY, SUPABASE_URL

logger = logging.getLogger(__name__)

_client: Client | None = None
# In-memory stores used when Supabase credentials are not configured yet
_memory_incidents: dict[str, dict[str, Any]] = {}
_memory_agent_runs: list[dict[str, Any]] = []


def get_db() -> Client | None:
    """Return the Supabase client instance if configured."""
    global _client
    if _client is not None:
        return _client
    if SUPABASE_URL and SUPABASE_SERVICE_KEY:
        try:
            _client = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)
            return _client
        except Exception as e:
            logger.error("Failed to initialize Supabase client: %s", e)
    return None


class _DbProxy:
    """Proxy object allowing db.table(...) syntax while supporting lazy connection."""

    def table(self, name: str):
        client = get_db()
        if client:
            return client.table(name)
        raise RuntimeError(
            "Supabase client is not configured. Please set SUPABASE_SERVICE_KEY in .env"
        )


db = _DbProxy()


def log_run(
    incident_id: str,
    agent: str,
    kind: str,
    inp: dict | None = None,
    out: dict | None = None,
    error: str | None = None,
) -> None:
    """Log an agent interaction to the agent_runs table or memory fallback."""
    payload = {
        "incident_id": incident_id,
        "agent": agent,
        "kind": kind,
        "input": inp,
        "output": out,
        "error": error,
    }
    client = get_db()
    if client:
        try:
            client.table("agent_runs").insert(payload).execute()
            return
        except Exception as e:
            logger.warning("Failed to insert into agent_runs in Supabase: %s", e)
    _memory_agent_runs.append(payload)


def update_incident(incident_id: str, **fields: Any) -> None:
    """Update incident record fields in Supabase or memory store."""
    client = get_db()
    if client:
        try:
            fields_to_update = dict(fields)
            fields_to_update["updated_at"] = "now()"
            client.table("incidents").update(fields_to_update).eq("id", incident_id).execute()
            return
        except Exception as e:
            logger.warning("Failed to update incident in Supabase: %s", e)
    if incident_id in _memory_incidents:
        _memory_incidents[incident_id].update(fields)


def get_incident(incident_id: str) -> dict[str, Any] | None:
    """Fetch an incident by its UUID."""
    client = get_db()
    if client:
        try:
            res = client.table("incidents").select("*").eq("id", incident_id).single().execute()
            return res.data
        except Exception as e:
            logger.warning("Failed to fetch incident from Supabase: %s", e)
    return _memory_incidents.get(incident_id)
