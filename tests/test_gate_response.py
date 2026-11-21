"""Tests for incident gate response validation and per-incident concurrency locking."""

from unittest.mock import MagicMock

import pytest
from fastapi import BackgroundTasks, HTTPException

import savageops.main as sm


def test_gate_validation_rejects_wrong_action():
    sm.get_incident = MagicMock()
    # Gate 1 incident awaiting user decision
    sm.get_incident.return_value = {
        "id": "inc-gate-1",
        "status": "awaiting_user",
        "pending": {"gate": 1},
    }

    # Gate 1 allows only approve / reject; submitting push must fail with 409
    with pytest.raises(HTTPException) as exc_info:
        sm.respond_to_incident(
            "inc-gate-1",
            {"action": "push"},
            BackgroundTasks(),
            admin={"sub": "admin-1"},
        )
    assert exc_info.value.status_code == 409
    assert exc_info.value.detail == "Response does not match the pending gate"


def test_gate_validation_rejects_double_response():
    sm.get_incident = MagicMock()
    # Incident has already moved past Gate 1 to status 'fixing'
    sm.get_incident.return_value = {
        "id": "inc-gate-1-double",
        "status": "fixing",
        "pending": {"gate": 1},
    }

    with pytest.raises(HTTPException) as exc_info:
        sm.respond_to_incident(
            "inc-gate-1-double",
            {"action": "approve", "choice": "permanent"},
            BackgroundTasks(),
            admin={"sub": "admin-1"},
        )
    assert exc_info.value.status_code == 409
    assert exc_info.value.detail == "This gate is no longer awaiting a response"


def test_per_incident_lock_is_registered():
    incident_id = "inc-lock-test"
    sm.get_incident = MagicMock()
    sm.get_incident.return_value = {
        "id": incident_id,
        "status": "awaiting_user",
        "pending": {"gate": 1},
    }

    # Calling respond_to_incident registers the lock for the incident_id
    try:
        sm.respond_to_incident(
            incident_id,
            {"action": "invalid"},
            BackgroundTasks(),
            admin={"sub": "admin-1"},
        )
    except HTTPException:
        pass

    assert incident_id in sm._incident_locks
