"""LangGraph orchestration graph for SavageOps multi-agent lifecycle."""

from typing import TypedDict

from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt

from savageops.agents.analyst import run_analyst
from savageops.agents.fixer import run_fix_draft, run_push
from savageops.agents.spokesperson import run_spokesperson
from savageops.agents.triage import run_triage
from savageops.config import MAX_HOPS
from savageops.db import update_incident
from savageops.metrics import ESCALATIONS, timed
from savageops.security import redact_untrusted_data


class State(TypedDict, total=False):
    incident_id: str
    alert: dict
    triage: dict
    info_request: dict | None
    analysis: dict
    summary: dict
    # Gate 1 response: {"action": "approve"|"reject", "choice": "fast"|"permanent"}
    user_decision: dict
    push_decision: dict  # Gate 2 response: {"action": "push"|"revise"|"reject", "feedback": ...}
    choice: str  # "fast" | "permanent"
    feedback: str
    rejected_solutions: list
    fix_draft: dict
    pr_url: str
    info_requests: int
    rejections: int
    revisions: int
    hops: int


def over_budget(s: State) -> bool:
    return s.get("hops", 0) >= MAX_HOPS


# --- Node Definitions ---


@timed("triage")
def triage_node(s: State) -> dict:
    r = run_triage(s["incident_id"], s["alert"], s.get("info_request"))
    return {
        "triage": r.model_dump(),
        "info_request": None,
        "hops": s.get("hops", 0) + 1,
    }


@timed("analyst")
def analyst_node(s: State) -> dict:
    res = run_analyst(
        s["incident_id"],
        s["triage"],
        s.get("rejected_solutions"),
        s.get("feedback"),
    )
    if res.info_request:
        return {
            "info_request": res.info_request.model_dump(),
            "info_requests": s.get("info_requests", 0) + 1,
            "hops": s.get("hops", 0) + 1,
        }
    return {
        "analysis": res.analysis.model_dump(),
        "info_request": None,
        "hops": s.get("hops", 0) + 1,
    }


@timed("spokesperson")
def spokesperson_node(s: State) -> dict:
    summ = run_spokesperson(s["incident_id"], s["triage"], s["analysis"]).model_dump()
    update_incident(
        s["incident_id"],
        status="awaiting_user",
        pending={"gate": 1, "summary": summ, "analysis": s["analysis"]},
    )
    return {"summary": summ, "hops": s.get("hops", 0) + 1}


def gate1_node(s: State) -> dict:
    d = interrupt({"gate": 1})
    return {"user_decision": d}


def after_gate1_node(s: State) -> dict:
    d = s.get("user_decision", {})
    if d.get("action") == "approve":
        return {"choice": d.get("choice", "permanent"), "feedback": ""}
    fast_title = s["analysis"]["fast_fix"]["title"]
    perm_title = s["analysis"]["permanent_fix"]["title"]
    rejected = s.get("rejected_solutions", []) + [fast_title, perm_title]
    return {
        "rejections": s.get("rejections", 0) + 1,
        "feedback": d.get("feedback", ""),
        "rejected_solutions": rejected,
    }


@timed("fixer")
def fixer_node(s: State) -> dict:
    draft = run_fix_draft(
        s["incident_id"],
        s["analysis"],
        s.get("choice", "permanent"),
        s.get("feedback"),
    )
    return {"fix_draft": draft, "hops": s.get("hops", 0) + 1}


def gate2_node(s: State) -> dict:
    d = interrupt({"gate": 2})
    return {"push_decision": d}


def revise_node(s: State) -> dict:
    push_dec = s.get("push_decision", {})
    return {
        "feedback": push_dec.get("feedback", ""),
        "revisions": s.get("revisions", 0) + 1,
    }


@timed("push")
def push_node(s: State) -> dict:
    url = run_push(s["incident_id"], s["analysis"], s["choice"], s["fix_draft"])
    return {"pr_url": url}


def escalate_node(s: State) -> dict:
    ESCALATIONS.inc()
    update_incident(s["incident_id"], status="needs_human")
    return {}


def done_rejected(s: State) -> dict:
    update_incident(s["incident_id"], status="rejected")
    return {}


def mark_not_applicable(s: State) -> dict:
    update_incident(s["incident_id"], status="not_applicable", pending=None)
    return {}


# --- Routing Logic ---


def route_after_triage(s: State) -> str:
    if not s["triage"].get("applicable", True):
        return "not_applicable"
    if s["triage"]["confidence"] < 0.6 or over_budget(s):
        return "escalate"
    return "analyst"


def route_after_analyst(s: State) -> str:
    if over_budget(s):
        return "escalate"
    if s.get("info_request"):
        return "triage" if s.get("info_requests", 0) <= 2 else "escalate"
    return "spokesperson"


def route_after_gate1(s: State) -> str:
    action = s.get("user_decision", {}).get("action")
    if action == "approve":
        return "fixer"
    return "analyst" if s.get("rejections", 0) < 3 and not over_budget(s) else "escalate"


def route_after_fixer(s: State) -> str:
    return "gate2" if s.get("fix_draft", {}).get("ok") else "escalate"


def route_after_gate2(s: State) -> str:
    action = s.get("push_decision", {}).get("action")
    if action == "push":
        return "push"
    if action == "revise":
        return "revise" if s.get("revisions", 0) < 2 else "escalate"
    return "done_rejected"


# --- Graph Construction ---

builder = StateGraph(State)

for name, fn in [
    ("triage", triage_node),
    ("analyst", analyst_node),
    ("spokesperson", spokesperson_node),
    ("gate1", gate1_node),
    ("after_gate1", after_gate1_node),
    ("fixer", fixer_node),
    ("gate2", gate2_node),
    ("revise", revise_node),
    ("push", push_node),
    ("escalate", escalate_node),
    ("done_rejected", done_rejected),
    ("not_applicable", mark_not_applicable),
]:
    builder.add_node(name, fn)

builder.add_edge(START, "triage")
builder.add_conditional_edges("triage", route_after_triage)
builder.add_conditional_edges("analyst", route_after_analyst)
builder.add_edge("spokesperson", "gate1")
builder.add_edge("gate1", "after_gate1")
builder.add_conditional_edges("after_gate1", route_after_gate1)
builder.add_conditional_edges("fixer", route_after_fixer)
builder.add_conditional_edges("gate2", route_after_gate2)
builder.add_edge("revise", "fixer")
builder.add_edge("push", END)
builder.add_edge("escalate", END)
builder.add_edge("done_rejected", END)
builder.add_edge("not_applicable", END)

checkpointer = MemorySaver()
graph = builder.compile(checkpointer=checkpointer)


def configure_postgres_checkpointer(checkpointer_obj) -> None:
    """Replace the local memory checkpointer with a durable PostgreSQL checkpointer."""
    global graph
    graph = builder.compile(checkpointer=checkpointer_obj)


def run_pipeline(incident_id: str, alert: dict):
    """Invoke the LangGraph pipeline for an incident in a separate thread context."""
    cfg = {"configurable": {"thread_id": incident_id}}
    graph.invoke({"incident_id": incident_id, "alert": redact_untrusted_data(alert)}, cfg)
