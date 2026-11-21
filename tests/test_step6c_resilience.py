from unittest.mock import MagicMock, patch

import httpx
import pytest
from pydantic import ValidationError

from savageops.models import FixedFile, InfoRequest, PatchHunk, TriageReport
from savageops.security import redact_untrusted_text
from savageops.tools import grafana, loki


def test_triage_report_validation_strict_value_error():
    # 1. Non-applicable report with 0, 0 lines must succeed
    report_not_applicable = TriageReport(
        file_path="app.py",
        line_start=0,
        line_end=0,
        error_summary="No traceback in logs",
        severity="P4",
        severity_reason="Informational",
        evidence=[],
        confidence=0.0,
        applicable=False,
    )
    assert report_not_applicable.applicable is False
    assert report_not_applicable.line_start == 0
    assert report_not_applicable.line_end == 0

    # 2. Applicable report with line_start < 1 must RAISE ValidationError (no silent clamp)
    with pytest.raises(ValidationError, match="line_start must be >= 1"):
        TriageReport(
            file_path="app.py",
            line_start=0,
            line_end=0,
            error_summary="Traceback found",
            severity="P2",
            severity_reason="Production error",
            evidence=["Error"],
            confidence=0.9,
            applicable=True,
        )

    # 3. Inverted line range line_start > line_end must RAISE ValidationError
    with pytest.raises(ValidationError, match="line_end must be >= line_start"):
        TriageReport(
            file_path="app.py",
            line_start=15,
            line_end=10,
            error_summary="Inverted range",
            severity="P3",
            severity_reason="Logic error",
            evidence=["Error"],
            confidence=0.8,
            applicable=True,
        )

    # 4. InfoRequest bounds on minutes (1 <= minutes <= 1440)
    with pytest.raises(ValidationError):
        InfoRequest(question="more logs", minutes=0)
    with pytest.raises(ValidationError):
        InfoRequest(question="more logs", minutes=2000)
    valid_info = InfoRequest(question="more logs", minutes=30)
    assert valid_info.minutes == 30

    # 5. FixedFile commit_message min_length >= 1
    with pytest.raises(ValidationError):
        FixedFile(
            hunks=[PatchHunk(old_text="bad", new_text="good")],
            commit_message="",
        )


def test_security_redact_phone_preserves_iso_dates_and_redacts_new_tokens():
    # 1. ISO date intact while phone number is redacted
    log_line = "2026-11-21 19:45:00 ERROR call +1 555-123-4567 for support"
    redacted = redact_untrusted_text(log_line)
    assert "2026-11-21" in redacted
    assert "+1 555-123-4567" not in redacted
    assert "[REDACTED]" in redacted

    # 2. Gemini API key
    gemini_key = "AIzaSyD" + "A" * 32
    assert gemini_key not in redact_untrusted_text(f"gemini_api_key={gemini_key}")

    # 3. Groq API key
    groq_key = "gsk_" + "B" * 24
    assert groq_key not in redact_untrusted_text(f"groq_key={groq_key}")

    # 4. GitHub fine-grained PAT
    gh_pat = "github_pat_" + "C" * 30
    assert gh_pat not in redact_untrusted_text(f"gh_pat={gh_pat}")

    # 5. JWT token
    jwt = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.SflKxwRJSMeKKF2QT4fwpMeJf36POk6yJV_adQssw5c"
    assert jwt not in redact_untrusted_text(f"Bearer {jwt}")


def test_loki_none_labels_and_real_error_no_fixture_fallback(monkeypatch):
    # 1. labels: None should not crash
    with patch("httpx.get") as mock_get:
        mock_resp = MagicMock()
        mock_resp.raise_for_status.return_value = None
        mock_resp.json.return_value = {"data": {"result": []}}
        mock_get.return_value = mock_resp

        logs = loki.get_logs({"labels": None})
        assert logs == ""

    # 2. Real Loki failure in non-mock mode must raise RuntimeError, not return fake fixture
    monkeypatch.delenv("SAVAGEOPS_MOCK", raising=False)
    with patch("savageops.tools.loki.LOKI_URL", "http://loki:3100"), \
         patch("savageops.tools.loki.httpx.get", side_effect=httpx.ConnectError("Connection refused")):
        with pytest.raises(RuntimeError, match="Failed to query Loki"):
            loki.get_logs({"labels": {"service": "my-service"}})

    # 3. In explicit mock mode, fallback to fixture is allowed
    monkeypatch.setenv("SAVAGEOPS_MOCK", "1")
    with patch("savageops.tools.loki.LOKI_URL", "http://loki:3100"), \
         patch("savageops.tools.loki.httpx.get", side_effect=httpx.ConnectError("Connection refused")):
        logs_mock = loki.get_logs({"labels": {"service": "my-service"}})
        assert len(logs_mock) > 0


def test_grafana_still_firing_guards_empty_fingerprint():
    with patch("savageops.tools.grafana.get_alert") as mock_get_alert:
        # Blank fingerprint returns True without querying get_alert
        assert grafana.still_firing("") is True
        assert not mock_get_alert.called
