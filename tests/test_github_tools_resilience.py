"""Tests for GitHub tools and fixer repo forwarding resilience."""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from savageops.agents import fixer
from savageops.tools import github_tools


def test_recent_commits_omits_sha_when_ref_is_none():
    """recent_commits must omit sha kwarg when ref=None, and pass sha when ref is present."""
    mock_client = MagicMock()
    mock_repo = MagicMock()
    mock_client.get_repo.return_value = mock_repo
    mock_repo.get_commits.return_value = []

    with patch.object(github_tools, "_get_client_and_repo", return_value=(mock_client, mock_repo)):
        # 1. ref is None -> sha must not be passed
        github_tools.recent_commits("app/pricing.py", ref=None)
        assert mock_repo.get_commits.call_args is not None
        _, kwargs_none = mock_repo.get_commits.call_args
        assert "sha" not in kwargs_none, f"'sha' should be omitted when ref=None, got kwargs={kwargs_none}"

        # 2. ref is "abc123" -> sha="abc123"
        github_tools.recent_commits("app/pricing.py", ref="abc123")
        _, kwargs_ref = mock_repo.get_commits.call_args
        assert kwargs_ref.get("sha") == "abc123"


def test_get_ci_check_runs_handles_pygithub_objects_and_none():
    """get_ci_check_runs safely handles PyGithub CheckRunOutput object and output=None."""
    mock_client = MagicMock()
    mock_repo = MagicMock()
    mock_commit = MagicMock()
    mock_repo.get_commit.return_value = mock_commit

    # Run 1: PyGithub object output (has .title, .summary)
    run_obj = MagicMock()
    run_obj.name = "tests"
    run_obj.status = "completed"
    run_obj.conclusion = "success"
    run_obj.output = SimpleNamespace(title="Pytest Suite", summary="12 passed")

    # Run 2: output is None
    run_none = MagicMock()
    run_none.name = "lint"
    run_none.status = "completed"
    run_none.conclusion = "success"
    run_none.output = None

    # Run 3: output is dict (for backwards-compatibility with mocks)
    run_dict = MagicMock()
    run_dict.name = "security"
    run_dict.status = "completed"
    run_dict.conclusion = "success"
    run_dict.output = {"title": "Security Scan", "summary": "0 alerts"}

    mock_commit.get_check_runs.return_value = [run_obj, run_none, run_dict]

    with patch.object(github_tools, "_get_client_and_repo", return_value=(mock_client, mock_repo)):
        results = github_tools.get_ci_check_runs("commit-sha-123")
        assert len(results) == 3
        assert results[0]["title"] == "Pytest Suite"
        assert results[0]["summary"] == "12 passed"
        assert results[1]["title"] is None
        assert results[1]["summary"] is None
        assert results[2]["title"] == "Security Scan"
        assert results[2]["summary"] == "0 alerts"


def test_wait_for_ci_status_grace_period_polls_again_before_none():
    """wait_for_ci_status does not return 'none' immediately on first empty poll if grace remains."""
    poll_calls = []

    def mock_get_runs(commit_sha, repo_name=None):
        poll_calls.append(commit_sha)
        if len(poll_calls) == 1:
            return []  # Initial poll: Actions not registered yet
        return [
            {
                "name": "tests",
                "status": "completed",
                "conclusion": "success",
                "title": "Pass",
                "summary": "ok",
            }
        ]

    with patch.object(github_tools, "get_ci_check_runs", side_effect=mock_get_runs):
        with patch("time.sleep", return_value=None):
            result = github_tools.wait_for_ci_status(
                "commit-123",
                timeout_seconds=30,
                poll_interval=2,
                no_checks_grace_seconds=10.0,
            )
            assert result["status"] == "success"
            assert len(poll_calls) == 2


def test_run_push_forwards_custom_repository(monkeypatch):
    """run_push forwards custom triage repository name to all github_tools operations."""
    test_repo = "custom-org/custom-repo"
    inc = {
        "id": "inc-repo-forward-test",
        "triage": {"repository": test_repo, "file_path": "src/app.py"},
    }
    analysis = {
        "root_cause": "bug in app",
        "permanent_fix": {"title": "fix bug", "risk": "low"},
    }
    draft = {
        "path": "src/app.py",
        "new_content": "fixed code",
        "commit_message": "fix app",
        "base_sha": "base123",
    }

    open_pr_called_with = {}
    wait_ci_called_with = []
    update_file_called_with = []

    def mock_open_pr(**kwargs):
        open_pr_called_with.update(kwargs)
        return {
            "pr_url": "https://github.com/custom-org/custom-repo/pull/1",
            "branch": "ai-fix-inc-repo",
            "head_sha": "head-sha-1",
            "pr_number": "1",
        }

    def mock_wait_ci(commit_sha, **kwargs):
        wait_ci_called_with.append((commit_sha, kwargs))
        if len(wait_ci_called_with) == 1:
            return {
                "status": "failure",
                "failure_details": "test failed",
                "runs": [{"name": "ci", "status": "completed", "conclusion": "failure", "summary": "fail"}],
            }
        return {"status": "success", "runs": []}

    def mock_update_file(path, new_content, commit_msg, branch_name, repo_name=None):
        update_file_called_with.append((path, repo_name))
        return "retry-sha-2"

    monkeypatch.setattr(fixer, "get_incident", lambda _id: inc)
    monkeypatch.setattr(fixer, "update_incident", lambda *args, **kwargs: None)
    monkeypatch.setattr(fixer, "log_run", lambda *args, **kwargs: None)
    monkeypatch.setattr(fixer, "open_pr", mock_open_pr)
    monkeypatch.setattr(fixer, "wait_for_ci_status", mock_wait_ci)
    monkeypatch.setattr(fixer, "update_branch_file", mock_update_file)
    monkeypatch.setattr(fixer, "apply_hunks", lambda code, hunks: "retry content")
    monkeypatch.setattr(fixer, "validate", lambda *args: None)
    monkeypatch.setattr(fixer, "_build_and_verify_diff", lambda *args: "diff")
    monkeypatch.setattr(
        fixer,
        "invoke_guarded",
        lambda *args: SimpleNamespace(hunks=[], commit_message="fix ci"),
    )

    url = fixer.run_push("inc-repo-forward-test", analysis, "permanent", draft)
    assert url == "https://github.com/custom-org/custom-repo/pull/1"

    # Verify repo_name was forwarded to open_pr
    assert open_pr_called_with.get("repo_name") == test_repo
    # Verify repo_name was forwarded to wait_for_ci_status
    assert wait_ci_called_with[0][1].get("repo_name") == test_repo
    # Verify repo_name was forwarded to update_branch_file
    assert update_file_called_with[0][1] == test_repo
