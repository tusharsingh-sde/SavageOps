"""Tests for CI loop, path safety, and fixer validation."""

from unittest.mock import patch

import pytest

from savageops.agents.fixer import ALLOWED_EXT, apply_hunks, validate
from savageops.tools.github_tools import sanitize_markdown, validate_safe_path, wait_for_ci_status


def test_path_safety_traversal():
    # Valid safe paths
    validate_safe_path("app.py")
    validate_safe_path("services/order_service.py")
    validate_safe_path("src/core/utils.py")

    # Directory traversal attempts
    with pytest.raises(ValueError, match="Restricted or invalid"):
        validate_safe_path("../secret.py")

    with pytest.raises(ValueError, match="Restricted or invalid"):
        validate_safe_path("app/../../etc/passwd")

    with pytest.raises(ValueError, match="Restricted or invalid"):
        validate_safe_path("/app.py")

    with pytest.raises(ValueError, match="Restricted or invalid"):
        validate_safe_path("\\app.py")

    # Restricted GitHub workflows / internal config attempts
    with pytest.raises(ValueError, match="Restricted or invalid"):
        validate_safe_path(".github/workflows/deploy.yml")

    with pytest.raises(ValueError, match="Restricted or invalid"):
        validate_safe_path("subfolder/.github/action.yml")

    for path in ("auth.py", "settings.py", "db/migrations/001.sql", "tests/test_app.py", "Dockerfile"):
        with pytest.raises(ValueError, match="Restricted or invalid"):
            validate_safe_path(path)


def test_allowed_extensions():
    assert ".py" in ALLOWED_EXT
    assert ".sh" not in ALLOWED_EXT
    assert ".yaml" not in ALLOWED_EXT


def test_pr_text_neutralizes_mentions_and_links():
    result = sanitize_markdown("@here https://example.com/path www.example.org")
    assert "@here" not in result
    assert "https://" not in result
    assert "www." not in result


def test_code_validation_syntax():
    valid_code = "def add(a, b):\n    return a + b\n"
    validate("test.py", valid_code)  # Should not raise

    invalid_code = "def broken(a, b\n    return a +"
    with pytest.raises(SyntaxError):
        validate("test.py", invalid_code)


def test_exact_hunks_preserve_crlf_and_final_newline():
    original = "def value():\r\n    return 1\r\n"
    result = apply_hunks(original, [{"old_text": "return 1", "new_text": "return 2"}])
    assert result == "def value():\r\n    return 2\r\n"
    with pytest.raises(ValueError, match="exactly once"):
        apply_hunks(original + "# return 1\r\n", [{"old_text": "return 1", "new_text": "return 3"}])


@patch("savageops.tools.github_tools.get_ci_check_runs")
def test_wait_for_ci_status_success(mock_runs):
    mock_runs.return_value = [
        {
            "name": "pytest",
            "status": "completed",
            "conclusion": "success",
            "summary": "All tests passed",
        }
    ]
    res = wait_for_ci_status("sha123", timeout_seconds=10, poll_interval=1)
    assert res["status"] == "success"


@patch("savageops.tools.github_tools.get_ci_check_runs")
def test_wait_for_ci_status_failure(mock_runs):
    mock_runs.return_value = [
        {
            "name": "pytest",
            "status": "completed",
            "conclusion": "failure",
            "summary": "AssertionError line 42",
        }
    ]
    res = wait_for_ci_status("sha123", timeout_seconds=10, poll_interval=1)
    assert res["status"] == "failure"
    assert "pytest: failure" in res["failure_details"]
