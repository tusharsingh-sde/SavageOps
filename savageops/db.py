"""Supabase database access with an explicitly enabled development test store."""

import logging
import os
from datetime import UTC, datetime
from typing import Any

from supabase import Client, create_client

from savageops.config import APP_ENV, SUPABASE_SERVICE_KEY, SUPABASE_URL
from savageops.security import redact_untrusted_data

logger = logging.getLogger(__name__)

_client: Client | None = None
# The in-memory store is available only under SAVAGEOPS_MOCK=1 outside production.
_memory_incidents: dict[str, dict[str, Any]] = {}
_memory_agent_runs: list[dict[str, Any]] = []


def _mock_enabled() -> bool:
    """Return true only for an explicit non-production mock-mode opt-in."""
    return os.getenv("SAVAGEOPS_MOCK") == "1" and APP_ENV != "production"


def get_db() -> Client | None:
    """Return Supabase client or allow the explicit mock store; never hide production failure."""
    if _mock_enabled():
        return None
    global _client
    if _client is not None:
        return _client
    if not SUPABASE_URL or not SUPABASE_SERVICE_KEY:
        raise RuntimeError("Supabase URL and service key are required; set SAVAGEOPS_MOCK=1 only for local tests")
    try:
        _client = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)
        return _client
    except Exception as e:
        if _mock_enabled():
            logger.warning("Supabase unavailable in explicit mock mode: %s", e)
            return None
        raise RuntimeError(f"Supabase client initialization failed: {e}") from e


class _MemoryQueryResult:
    def __init__(self, data: list[dict[str, Any]]):
        self.data = data


class _MemoryTableQuery:
    def __init__(self, table_name: str):
        self.table_name = table_name
        self._filters: list[tuple[str, str, Any]] = []
        self._limit_val: int | None = None
        self._select_fields: str = "*"

    def select(self, fields: str = "*"):
        self._select_fields = fields
        return self

    def eq(self, column: str, value: Any):
        self._filters.append((column, "eq", value))
        return self

    def gte(self, column: str, value: Any):
        self._filters.append((column, "gte", value))
        return self

    def in_(self, column: str, values: list[Any]):
        self._filters.append((column, "in", values))
        return self

    def limit(self, n: int):
        self._limit_val = n
        return self

    def single(self):
        res = self.execute()
        first = res.data[0] if res.data else None
        return _MemoryQueryResult([first] if first else [])

    def _matches(self, row: dict[str, Any]) -> bool:
        for col, op, val in self._filters:
            row_val = row.get(col)
            if op == "eq" and row_val != val:
                return False
            if op == "gte":
                if str(row_val) < str(val):
                    return False
            if op == "in" and row_val not in val:
                return False
        return True

    def execute(self) -> _MemoryQueryResult:
        store = _memory_incidents if self.table_name == "incidents" else {}
        rows = [row for row in store.values() if self._matches(row)]
        if self._limit_val is not None:
            rows = rows[: self._limit_val]
        return _MemoryQueryResult(rows)

    def insert(self, payload: dict[str, Any]):
        class _InsertExec:
            def __init__(self, t_name: str, p: dict[str, Any]):
                self.t_name = t_name
                self.p = p

            def execute(self):
                now_iso = datetime.now(UTC).isoformat()
                row = dict(self.p)
                if self.t_name == "incidents":
                    row_id = row.get("id") or f"mem-{len(_memory_incidents) + 1}"
                    row["id"] = row_id
                    row.setdefault("occurrences", 1)
                    row.setdefault("status", "received")
                    row.setdefault("created_at", now_iso)
                    row.setdefault("updated_at", now_iso)
                    row.setdefault("last_seen_at", now_iso)
                    # Check fingerprint unique constraint
                    fp = row.get("fingerprint")
                    if fp and any(r.get("fingerprint") == fp for r in _memory_incidents.values()):
                        raise ValueError(f"Duplicate fingerprint: {fp}")
                    _memory_incidents[row_id] = row
                    return _MemoryQueryResult([row])
                elif self.t_name == "agent_runs":
                    _memory_agent_runs.append(row)
                    return _MemoryQueryResult([row])
                return _MemoryQueryResult([row])

        return _InsertExec(self.table_name, payload)

    def update(self, updates: dict[str, Any]):
        class _UpdateExec:
            def __init__(self, query: "_MemoryTableQuery", u: dict[str, Any]):
                self.query = query
                self.u = u

            def eq(self, column: str, value: Any):
                self.query.eq(column, value)
                return self

            def execute(self):
                now_iso = datetime.now(UTC).isoformat()
                applied = []
                store = _memory_incidents if self.query.table_name == "incidents" else {}
                for row_id, row in list(store.items()):
                    if self.query._matches(row):
                        row.update(self.u)
                        row["updated_at"] = now_iso
                        applied.append(row)
                return _MemoryQueryResult(applied)

        return _UpdateExec(self, updates)


class _DbProxy:
    """Proxy object allowing db.table(...) syntax with explicit mock-only memory behavior."""

    def table(self, name: str):
        client = get_db()
        if client:
            return client.table(name)
        if _mock_enabled():
            return _MemoryTableQuery(name)
        raise RuntimeError("In-memory database is disabled; set SAVAGEOPS_MOCK=1 only for local tests")


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
    incident = get_incident(incident_id)
    if not incident or incident.get("source") != "canary":
        try:
            from savageops.metrics import AGENT_RUNS

            AGENT_RUNS.labels(agent=agent, kind=kind).inc()
        except Exception:
            pass

    payload = {
        "incident_id": incident_id,
        "agent": agent,
        "kind": kind,
        "input": redact_untrusted_data(inp),
        "output": redact_untrusted_data(out),
        "error": redact_untrusted_data(error),
    }
    client = get_db()
    if client:
        try:
            client.table("agent_runs").insert(payload).execute()
            return
        except Exception as e:
            if not _mock_enabled():
                raise RuntimeError(f"Supabase agent_runs insert failed: {e}") from e
            logger.warning("Using explicit mock store after agent_runs insert failure: %s", e)
    if _mock_enabled():
        _memory_agent_runs.append(payload)


def update_incident(incident_id: str, **fields: Any) -> None:
    """Update incident record fields in Supabase or memory store."""
    client = get_db()
    now_iso = datetime.now(UTC).isoformat()
    fields_to_update = dict(fields)
    fields_to_update["updated_at"] = now_iso
    if "status" in fields:
        fields_to_update.setdefault("alerted_at", None)

    if client:
        try:
            client.table("incidents").update(fields_to_update).eq("id", incident_id).execute()
            return
        except Exception as e:
            if not _mock_enabled():
                raise RuntimeError(f"Supabase update failed: {e}") from e
            logger.warning("Using explicit mock store after incident update failure: %s", e)
    if _mock_enabled() and incident_id in _memory_incidents:
        _memory_incidents[incident_id].update(fields_to_update)


def get_incident(incident_id: str) -> dict[str, Any] | None:
    """Fetch an incident by its UUID."""
    client = get_db()
    if client:
        try:
            res = client.table("incidents").select("*").eq("id", incident_id).single().execute()
            return res.data
        except Exception as e:
            if not _mock_enabled():
                raise RuntimeError(f"Supabase incident fetch failed: {e}") from e
            logger.warning("Using explicit mock store after incident fetch failure: %s", e)
    if _mock_enabled():
        return _memory_incidents.get(incident_id)
    raise RuntimeError("In-memory database is disabled")
