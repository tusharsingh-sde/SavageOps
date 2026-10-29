"""Regression tests for structured model contracts and redaction safeguards."""

from importlib import resources

import pytest

from savageops.agents.triage import extract_path
from savageops.models import AnalystResult
from savageops.security import redact_untrusted_text, traceback_context


def test_analyst_result_requires_exactly_one_payload():
    with pytest.raises(ValueError, match="Exactly one"):
        AnalystResult()
    solution = {"title": "fix", "description": "fix it", "type": "patch", "risk": "low"}
    analysis = {
        "root_cause": "cause",
        "fast_fix": solution,
        "permanent_fix": solution,
        "recommendation": "permanent",
    }
    with pytest.raises(ValueError, match="Exactly one"):
        AnalystResult(analysis=analysis, info_request={"question": "more logs"})


def test_untrusted_text_redacts_secrets_and_email():
    redacted = redact_untrusted_text("api_key=abc123 user=ops@example.com")
    assert "abc123" not in redacted
    assert "ops@example.com" not in redacted


def test_traceback_context_keeps_exception_line_and_collapses_duplicates():
    logs = "Traceback (most recent call last):\n  File \"app.py\", line 9\n  File \"app.py\", line 9\nZeroDivisionError: bad\n"
    context = traceback_context(logs)
    assert "File \"app.py\", line 9" in context
    assert "ZeroDivisionError" in context
    assert "repeated 1 times" in context


def test_sample_fixture_has_stable_traceback_path():
    logs = (resources.files("savageops") / "fixtures" / "sample_logs.txt").read_text(
        encoding="utf-8"
    )
    assert extract_path(logs) == "app.py"
