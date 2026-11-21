from datetime import UTC
from unittest.mock import MagicMock, patch

import pytest
from pydantic import ValidationError

from savageops.models import PatchHunk, TriageReport
from savageops.security import redact_untrusted_data, redact_untrusted_text
from savageops.tools import grafana, loki


def test_triage_report_validation_applicable_and_not_applicable():
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

    # 2. Applicable report with line_start < 1 must raise ValidationError
    with pytest.raises(ValidationError):
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

    # 3. Inverted line range line_start > line_end must raise ValidationError
    with pytest.raises(ValidationError):
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


def test_patch_hunk_allows_empty_new_text_for_deletion():
    # Deletion hunk with empty replacement
    hunk = PatchHunk(old_text="obsolete_line()\n", new_text="")
    assert hunk.old_text == "obsolete_line()\n"
    assert hunk.new_text == ""

    # Old text still requires min_length >= 1
    with pytest.raises(ValidationError):
        PatchHunk(old_text="", new_text="replacement")


def test_security_redact_uri_passwords_and_tokens():
    # Connection string password redaction
    db_uri = "postgresql://my_user:SuperSecretPassword123@db.prod.internal:5432/orders"
    redacted_uri = redact_untrusted_text(db_uri)
    assert "SuperSecretPassword123" not in redacted_uri
    assert "postgresql://my_user:[REDACTED]@db.prod.internal:5432/orders" == redacted_uri

    # Normal URL without credentials must not be mangled
    normal_url = "https://github.com/tusharsingh-sde/SavageOps/pull/12"
    assert redact_untrusted_text(normal_url) == normal_url

    # Slack token with hyphens
    slack_token = "xoxb-" + "dummy-slack-token-sample-value"
    redacted_slack = redact_untrusted_text(f"Token is {slack_token}")
    assert slack_token not in redacted_slack
    assert "[REDACTED]" in redacted_slack

    # AWS access key
    aws_key = "AKIAIOSFODNN7EXAMPLE"
    redacted_aws = redact_untrusted_text(f"Key={aws_key}")
    assert aws_key not in redacted_aws
    assert "[REDACTED]" in redacted_aws


def test_security_redact_untrusted_data_preserves_tuple_and_set():
    secret = "sk-" + "A" * 30

    # Tuple type preservation
    tup = (secret, "hello")
    redacted_tup = redact_untrusted_data(tup)
    assert isinstance(redacted_tup, tuple)
    assert secret not in redacted_tup[0]
    assert "[REDACTED]" in redacted_tup[0]
    assert redacted_tup[1] == "hello"

    # Set type preservation
    st = {secret, "safe_value"}
    redacted_st = redact_untrusted_data(st)
    assert isinstance(redacted_st, set)
    assert any("[REDACTED]" in item for item in redacted_st)
    assert "safe_value" in redacted_st


def test_loki_logql_sanitization_and_naive_ts():
    # 1. Naive timestamp must get UTC tzinfo
    dt = loki._parse_timestamp("2026-01-01T00:00:00")
    assert dt.tzinfo == UTC

    # 2. Injection attempts in service or labels sanitized / handled safely
    with patch("httpx.get") as mock_get:
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.raise_for_status.return_value = None
        # Malformed nested payload handled safely
        mock_resp.json.return_value = {
            "data": {
                "result": [
                    "not-a-dict",
                    {
                        "stream": {"service": "test"},
                        "values": [
                            [1700000000000000000, "2026-01-01 Error in app.py"],
                        ],
                    },
                ]
            }
        }
        mock_get.return_value = mock_resp

        # Safe service query succeeds
        logs = loki.get_logs({"labels": {"service": "auth-service"}})
        assert "Error in app.py" in logs

        # Dangerous service with quotes and backslashes
        with pytest.raises(ValueError, match="Invalid service label"):
            loki.get_logs({"labels": {"service": 'auth" || injection'}})


def test_grafana_get_alert_filters_non_dict():
    with patch("httpx.get") as mock_get:
        mock_resp = MagicMock()
        mock_resp.raise_for_status.return_value = None
        mock_resp.json.return_value = [
            "invalid-string-element",
            None,
            {"fingerprint": "fp-123", "status": {"state": "firing"}},
        ]
        mock_get.return_value = mock_resp

        alert = grafana.get_alert("fp-123")
        assert alert is not None
        assert alert["fingerprint"] == "fp-123"

        # Fingerprint not found
        assert grafana.get_alert("unknown-fp") is None
