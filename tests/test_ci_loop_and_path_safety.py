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


def test_ci_status_timeout_default_and_env(monkeypatch):
    from savageops.agents import fixer
    assert fixer.CI_STATUS_TIMEOUT_SECONDS == 180


def test_run_push_ci_retry_rejects_dangerous_diff(monkeypatch):
    from types import SimpleNamespace
    from unittest.mock import Mock

    from langchain_core.runnables import RunnableLambda

    from savageops.agents import fixer

    monkeypatch.setattr(fixer, "get_incident", lambda _id: {"triage": {}})
    monkeypatch.setattr(fixer, "log_run", lambda *_a, **_kw: None)
    monkeypatch.setattr(
        fixer,
        "open_pr",
        lambda **_kwargs: {"pr_url": "https://example.invalid/pr/1", "branch": "b", "head_sha": "h"},
    )
    updates = []
    monkeypatch.setattr(fixer, "update_incident", lambda _id, **fields: updates.append(fields))
    ci = Mock(return_value={"status": "failure", "failure_details": "tests failed"})
    monkeypatch.setattr(fixer, "wait_for_ci_status", ci)
    monkeypatch.setattr(fixer, "get_llm", lambda: RunnableLambda(lambda value: value))
    monkeypatch.setattr(fixer, "prompt", RunnableLambda(lambda value: value))
    monkeypatch.setattr(
        fixer,
        "invoke_guarded",
        lambda *_a, **_kw: SimpleNamespace(
            hunks=[{"old_text": "return 1", "new_text": "import subprocess\n    return 1"}],
            commit_message="fix",
        ),
    )
    mock_update_branch = Mock(return_value="retry-sha")
    monkeypatch.setattr(fixer, "update_branch_file", mock_update_branch)

    solution = {"title": "fix", "risk": "low"}
    fixer.run_push(
        "incident",
        {"root_cause": "cause", "permanent_fix": solution},
        "permanent",
        {
            "path": "app.py",
            "new_content": "def f():\n    return 1\n",
            "commit_message": "fix",
            "base_sha": "base",
        },
    )
    assert mock_update_branch.call_count == 0
    assert any(u.get("status") == "needs_human" for u in updates)


def test_build_and_verify_diff_enforces_rules():
    from savageops.agents.fixer import _build_and_verify_diff

    # No changes
    with pytest.raises(ValueError, match="made no changes"):
        _build_and_verify_diff("a = 1\n", "a = 1\n")

    # Dangerous pattern
    with pytest.raises(ValueError, match="Draft adds network"):
        _build_and_verify_diff("a = 1\n", "import socket\na = 1\n")


def test_run_push_ci_retry_tracks_current_content(monkeypatch):
    from types import SimpleNamespace
    from unittest.mock import Mock

    from langchain_core.runnables import RunnableLambda

    from savageops.agents import fixer

    monkeypatch.setattr(fixer, "MAX_CI_FIX_RETRIES", 2)
    monkeypatch.setattr(fixer, "get_incident", lambda _id: {"triage": {}})
    monkeypatch.setattr(fixer, "log_run", lambda *_a, **_kw: None)
    monkeypatch.setattr(
        fixer,
        "open_pr",
        lambda **_kwargs: {"pr_url": "https://example.invalid/pr/1", "branch": "b", "head_sha": "h"},
    )
    monkeypatch.setattr(fixer, "update_incident", lambda _id, **fields: None)
    ci = Mock(
        side_effect=[
            {"status": "failure", "failure_details": "fail 1"},
            {"status": "failure", "failure_details": "fail 2"},
            {"status": "success"},
        ]
    )
    monkeypatch.setattr(fixer, "wait_for_ci_status", ci)
    monkeypatch.setattr(fixer, "get_llm", lambda: RunnableLambda(lambda value: value))
    monkeypatch.setattr(fixer, "prompt", RunnableLambda(lambda value: value))

    hunk_responses = [
        SimpleNamespace(hunks=[{"old_text": "return 1", "new_text": "return 2"}], commit_message="fix 1"),
        SimpleNamespace(hunks=[{"old_text": "return 2", "new_text": "return 3"}], commit_message="fix 2"),
    ]
    monkeypatch.setattr(fixer, "invoke_guarded", lambda *_a, **_kw: hunk_responses.pop(0))
    pushed_contents = []
    monkeypatch.setattr(
        fixer,
        "update_branch_file",
        lambda path, content, msg, branch: pushed_contents.append(content) or "sha",
    )

    solution = {"title": "fix", "risk": "low"}
    fixer.run_push(
        "incident",
        {"root_cause": "cause", "permanent_fix": solution},
        "permanent",
        {
            "path": "app.py",
            "new_content": "def f():\n    return 1\n",
            "commit_message": "fix",
            "base_sha": "base",
        },
    )
    assert len(pushed_contents) == 2
    assert "return 2" in pushed_contents[0]
    assert "return 3" in pushed_contents[1]


