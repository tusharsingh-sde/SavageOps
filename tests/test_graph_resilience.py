"""Tests for graph checkpointer configuration, crash boundaries, and state resilience."""

from unittest.mock import patch

import pytest

from savageops.config import MAX_HOPS
from savageops.graph import (
    configure_postgres_checkpointer,
    done_rejected,
    escalate_node,
    get_graph,
    resume_pipeline,
    route_after_gate2,
    run_pipeline,
)
from savageops.metrics import ESCALATIONS


def test_configure_postgres_checkpointer_updates_get_graph():
    from langgraph.checkpoint.memory import MemorySaver

    initial_graph = get_graph()
    new_checkpointer = MemorySaver()
    configure_postgres_checkpointer(new_checkpointer)
    new_graph = get_graph()
    assert new_graph is not initial_graph

    # Reset back to MemorySaver for subsequent tests
    configure_postgres_checkpointer(MemorySaver())


def test_pipeline_crash_boundary_escalates_and_bumps_metric(monkeypatch):
    initial_escalations = ESCALATIONS._value.get()
    updates = []
    monkeypatch.setattr(
        "savageops.graph.update_incident",
        lambda inc_id, **fields: updates.append((inc_id, fields)),
    )

    with patch.object(get_graph(), "invoke", side_effect=RuntimeError("GitHub API 500")):
        with pytest.raises(RuntimeError, match="GitHub API 500"):
            run_pipeline("inc-crash-run", {"rule": "high_error_rate"})

    assert ESCALATIONS._value.get() == initial_escalations + 1
    assert updates == [
        (
            "inc-crash-run",
            {
                "status": "needs_human",
                "pending": {"error": "Pipeline crashed: GitHub API 500"},
            },
        )
    ]

    # Test resume_pipeline crash boundary as well
    updates.clear()
    with patch.object(get_graph(), "invoke", side_effect=RuntimeError("LLMs all down")):
        with pytest.raises(RuntimeError, match="LLMs all down"):
            resume_pipeline("inc-crash-resume", {"action": "approve"})

    assert ESCALATIONS._value.get() == initial_escalations + 2
    assert updates == [
        (
            "inc-crash-resume",
            {
                "status": "needs_human",
                "pending": {"error": "Pipeline crashed: LLMs all down"},
            },
        )
    ]


def test_done_rejected_clears_pending(monkeypatch):
    updates = []
    monkeypatch.setattr(
        "savageops.graph.update_incident",
        lambda inc_id, **fields: updates.append((inc_id, fields)),
    )
    done_rejected({"incident_id": "inc-rej-1"})
    assert updates == [("inc-rej-1", {"status": "rejected", "pending": None})]


def test_escalate_node_sets_meaningful_reason(monkeypatch):
    updates = []
    monkeypatch.setattr(
        "savageops.graph.update_incident",
        lambda inc_id, **fields: updates.append((inc_id, fields)),
    )

    # 1. Fixer failed with specific reason
    state_fixer_failed = {
        "incident_id": "inc-esc-1",
        "fix_draft": {"ok": False, "reason": "Draft adds dangerous network access"},
    }
    escalate_node(state_fixer_failed)
    assert updates[-1] == (
        "inc-esc-1",
        {
            "status": "needs_human",
            "pending": {"error": "Draft adds dangerous network access"},
        },
    )

    # 2. Hop budget exceeded
    state_over_budget = {
        "incident_id": "inc-esc-2",
        "hops": MAX_HOPS,
    }
    escalate_node(state_over_budget)
    assert updates[-1] == (
        "inc-esc-2",
        {
            "status": "needs_human",
            "pending": {"error": f"Incident exceeded hop budget ({MAX_HOPS})."},
        },
    )


def test_route_after_gate2_enforces_budget():
    # Revise within budget
    state_ok = {
        "push_decision": {"action": "revise"},
        "revisions": 1,
        "hops": MAX_HOPS - 1,
    }
    assert route_after_gate2(state_ok) == "revise"

    # Revise over budget must escalate
    state_over_budget = {
        "push_decision": {"action": "revise"},
        "revisions": 1,
        "hops": MAX_HOPS,
    }
    assert route_after_gate2(state_over_budget) == "escalate"
