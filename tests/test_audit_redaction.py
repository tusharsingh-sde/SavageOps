"""Regression coverage for sensitive values crossing LLM, database, and PR boundaries."""


from langchain_core.runnables import RunnableLambda

from savageops import db as db_module
from savageops.agents import fixer, triage
from savageops.db import db


def test_redaction_covers_prompt_agent_runs_and_pr_body(monkeypatch):
    monkeypatch.setenv("SAVAGEOPS_MOCK", "1")
    monkeypatch.setenv("APP_ENV", "development")
    monkeypatch.setattr(db_module, "SUPABASE_URL", "")
    monkeypatch.setattr(db_module, "SUPABASE_SERVICE_KEY", "")
    monkeypatch.setattr(db_module, "_client", None)
    db_module._memory_incidents.clear()
    db_module._memory_agent_runs.clear()

    secret_values = [
        "sk-" + "A" * 30,
        "ghp_" + "B" * 30,
        "private@example.com",
        "+1 (415) 555-0123",
    ]
    untrusted = " ".join(secret_values)
    logs = (
        'Traceback (most recent call last):\n  File "app.py", line 1, in f\n'
        f"ValueError: bad {untrusted}"
    )
    db.table("incidents").insert(
        {"id": "redaction-audit", "source": "webhook", "status": "received"}
    ).execute()

    monkeypatch.setattr(triage, "get_logs", lambda *_args, **_kwargs: logs)
    monkeypatch.setattr(triage, "get_file", lambda *_args, **_kwargs: "def f():\n    pass\n")
    seen_inputs = {}
    report = triage.TriageReport(
        file_path="app.py",
        line_start=1,
        line_end=1,
        error_summary=untrusted,
        severity="P3",
        severity_reason="test",
        evidence=["ValueError"],
        confidence=0.9,
    )
    monkeypatch.setattr(
        triage,
        "invoke_guarded",
        lambda _runnable, inputs, _incident_id: (seen_inputs.update(inputs) or report),
    )
    monkeypatch.setattr(triage, "get_llm", lambda: RunnableLambda(lambda value: value))
    monkeypatch.setattr(triage, "prompt", RunnableLambda(lambda value: value))
    triage.run_triage("redaction-audit", {"labels": {"service": "test-service"}})

    incident = db_module.get_incident("redaction-audit")
    monkeypatch.setattr(fixer, "get_incident", lambda _incident_id: incident)
    monkeypatch.setattr(fixer, "get_llm", lambda: RunnableLambda(lambda value: value))
    monkeypatch.setattr(fixer, "prompt", RunnableLambda(lambda value: value))
    captured_pr = {}
    monkeypatch.setattr(
        fixer,
        "open_pr",
        lambda **kwargs: (
            captured_pr.update(kwargs)
            or {"pr_url": "https://example.invalid/pr/1", "branch": "audit", "head_sha": "head"}
        ),
    )
    monkeypatch.setattr(fixer, "update_incident", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        fixer, "wait_for_ci_status", lambda *_args, **_kwargs: {"status": "none", "runs": []}
    )
    solution = {"title": untrusted, "risk": untrusted}
    fixer.run_push(
        "redaction-audit",
        {"root_cause": untrusted, "permanent_fix": solution},
        "permanent",
        {"path": "app.py", "new_content": "pass\n", "commit_message": "fix"},
    )

    output_surfaces = str(seen_inputs) + str(db_module._memory_agent_runs) + captured_pr["pr_body"]
    for secret in secret_values:
        assert secret not in output_surfaces
