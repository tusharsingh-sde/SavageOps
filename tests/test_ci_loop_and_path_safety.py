"""Tests for CI loop, path safety, and fixer validation."""

from unittest.mock import patch

import pytest

from savageops.agents.fixer import ALLOWED_EXT, validate
from savageops.tools.github_tools import validate_safe_path, wait_for_ci_status


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


def test_allowed_extensions():
    assert ".py" in ALLOWED_EXT
    assert ".sh" not in ALLOWED_EXT
    assert ".yaml" not in ALLOWED_EXT


def test_code_validation_syntax():
    valid_code = "def add(a, b):\n    return a + b\n"
    validate("test.py", valid_code)  # Should not raise

    invalid_code = "def broken(a, b\n    return a +"
    with pytest.raises(SyntaxError):
        validate("test.py", invalid_code)


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
