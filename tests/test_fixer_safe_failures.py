"""Bad or unusable fixer outputs must escalate and never reach the push node."""

from types import SimpleNamespace

import pytest
from langchain_core.runnables import RunnableLambda

from savageops.agents import fixer
from savageops.graph import route_after_fixer


@pytest.mark.parametrize(
    ("original", "hunks"),
    [
        ("def f():\n    return 1\n", [{"old_text": "absent", "new_text": "replacement"}]),
        (
            "def f():\n    return 1\n# return 1\n",
            [{"old_text": "return 1", "new_text": "return 2"}],
        ),
        ("def f():\n    return 1\n", []),
        ("def f():\n    return 1\n", [{"old_text": "return 1", "new_text": "return ("}]),
    ],
    ids=["hunk-zero-matches", "hunk-multiple-matches", "empty-llm-hunks", "ast-parse-fails"],
)
def test_bad_hunk_draft_sets_needs_human_and_cannot_push(monkeypatch, original, hunks):
    incident = {"triage": {"file_path": "src/module.py"}}
    monkeypatch.setattr(fixer, "get_incident", lambda _incident_id: incident)
    monkeypatch.setattr(fixer, "get_file_with_sha", lambda *_args, **_kwargs: (original, "blob-sha"))
    monkeypatch.setattr(fixer, "get_llm", lambda: RunnableLambda(lambda value: value))
    monkeypatch.setattr(fixer, "prompt", RunnableLambda(lambda value: value))
    monkeypatch.setattr(
        fixer,
        "invoke_guarded",
        lambda *_args, **_kwargs: SimpleNamespace(hunks=hunks, commit_message="fix"),
    )
    updates = []
    monkeypatch.setattr(
        fixer, "update_incident", lambda _incident_id, **fields: updates.append(fields)
    )
    monkeypatch.setattr(fixer, "log_run", lambda *_args, **_kwargs: None)
    analysis = {
        "root_cause": "cause",
        "permanent_fix": {
            "title": "fix",
            "description": "fix",
            "type": "patch",
            "risk": "low",
        },
    }

    result = fixer.run_fix_draft("incident", analysis, "permanent")

    assert result["ok"] is False
    assert any(update.get("status") == "needs_human" for update in updates)
    assert route_after_fixer({"fix_draft": result}) == "escalate"
