"""Regression tests for database, metrics, and CI audit requirements."""

from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest
from fastapi.testclient import TestClient
from langchain_core.runnables import RunnableLambda

from savageops import db as db_module
from savageops.agents import fixer
from savageops.main import app
from savageops.tools.github_tools import wait_for_ci_status


def test_database_production_does_not_fall_back_to_memory(monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("SAVAGEOPS_MOCK", "0")
    monkeypatch.setattr(db_module, "APP_ENV", "production", raising=False)
    monkeypatch.setattr(db_module, "SUPABASE_URL", "")
    monkeypatch.setattr(db_module, "SUPABASE_SERVICE_KEY", "")
    monkeypatch.setattr(db_module, "_client", None)
    with pytest.raises(RuntimeError, match="Supabase"):
        db_module.get_db()


def test_database_mock_flag_is_ignored_in_production(monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("SAVAGEOPS_MOCK", "1")
    monkeypatch.setattr(db_module, "APP_ENV", "production", raising=False)
    monkeypatch.setattr(db_module, "SUPABASE_URL", "")
    monkeypatch.setattr(db_module, "SUPABASE_SERVICE_KEY", "")
    monkeypatch.setattr(db_module, "_client", None)
    with pytest.raises(RuntimeError, match="Supabase"):
        db_module.get_db()


def test_database_operation_failure_in_production_raises(monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("SAVAGEOPS_MOCK", "0")
    monkeypatch.setattr(db_module, "APP_ENV", "production", raising=False)

    class BrokenQuery:
        def update(self, _fields):
            return self

        def eq(self, _column, _value):
            return self

        def execute(self):
            raise ConnectionError("database unavailable")

    class BrokenClient:
        def table(self, _name):
            return BrokenQuery()

    monkeypatch.setattr(db_module, "_client", BrokenClient())
    before = dict(db_module._memory_incidents)
    with pytest.raises(RuntimeError, match="Supabase update failed"):
        db_module.update_incident("production-test-id", status="failed")
    assert db_module._memory_incidents == before


def test_database_memory_store_requires_explicit_mock_flag(monkeypatch):
    monkeypatch.setenv("APP_ENV", "development")
    monkeypatch.setenv("SAVAGEOPS_MOCK", "1")
    monkeypatch.setattr(db_module, "APP_ENV", "development", raising=False)
    monkeypatch.setattr(db_module, "SUPABASE_URL", "")
    monkeypatch.setattr(db_module, "SUPABASE_SERVICE_KEY", "")
    monkeypatch.setattr(db_module, "_client", None)
    row = db_module.db.table("incidents").insert({"id": "mock-test", "status": "received"}).execute().data[0]
    assert row["id"] == "mock-test"


def test_database_without_mock_and_without_credentials_fails(monkeypatch):
    monkeypatch.setenv("APP_ENV", "development")
    monkeypatch.setenv("SAVAGEOPS_MOCK", "0")
    monkeypatch.setattr(db_module, "APP_ENV", "development", raising=False)
    monkeypatch.setattr(db_module, "SUPABASE_URL", "")
    monkeypatch.setattr(db_module, "SUPABASE_SERVICE_KEY", "")
    monkeypatch.setattr(db_module, "_client", None)
    with pytest.raises(RuntimeError, match="SAVAGEOPS_MOCK=1"):
        db_module.get_db()


def test_metrics_has_separate_scrape_token_and_public_healthcheck():
    with patch("savageops.main.METRICS_TOKEN", "scrape-token-audit"):
        client = TestClient(app)
        no_token = client.get("/metrics")
        with_token = client.get("/metrics", headers={"Authorization": "Bearer scrape-token-audit"})
        health = client.get("/health")
        deep_health = client.get("/health/deep")
    assert no_token.status_code == 401
    assert with_token.status_code == 200
    assert health.status_code == 200
    assert deep_health.status_code in {401, 403}


def test_docker_healthcheck_uses_public_health_endpoint():
    from pathlib import Path

    import yaml

    compose = yaml.safe_load(Path("deploy/docker-compose.yml").read_text(encoding="utf-8"))
    command = compose["services"]["savageops"]["healthcheck"]["test"]
    assert any("/health" in part and "/health/deep" not in part for part in command)


def test_wait_for_ci_reports_no_checks_and_timeout(monkeypatch):
    monkeypatch.setattr("savageops.tools.github_tools.get_ci_check_runs", lambda _sha: [])
    assert wait_for_ci_status("sha", timeout_seconds=1, poll_interval=1)["status"] == "none"
    monkeypatch.setattr(
        "savageops.tools.github_tools.get_ci_check_runs",
        lambda _sha: [{"name": "pending", "status": "queued", "conclusion": None}],
    )
    assert wait_for_ci_status("sha", timeout_seconds=0, poll_interval=1)["status"] == "timeout"


def test_run_push_records_no_ci_as_manual_review_not_pass(monkeypatch):
    monkeypatch.setattr(fixer, "get_incident", lambda _id: {"triage": {}})
    monkeypatch.setattr(
        fixer, "open_pr",
        lambda **_kwargs: {"pr_url": "https://example.invalid/pr/1", "branch": "b", "head_sha": "h"},
    )
    updates = []
    monkeypatch.setattr(fixer, "update_incident", lambda _id, **fields: updates.append(fields))
    ci = Mock(return_value={"status": "none", "runs": []})
    monkeypatch.setattr(fixer, "wait_for_ci_status", ci)
    solution = {"title": "fix", "risk": "low"}
    result = fixer.run_push("incident", {"root_cause": "cause", "permanent_fix": solution}, "permanent", {
        "path": "app.py", "new_content": "pass\n", "commit_message": "fix", "base_sha": "base",
    })
    assert result == "https://example.invalid/pr/1"
    assert any(item.get("ci_status") == "none" and item.get("status") == "needs_human" for item in updates)
    assert ci.call_args.kwargs["timeout_seconds"] == fixer.CI_STATUS_TIMEOUT_SECONDS


def test_run_push_ci_failure_retries_at_most_once(monkeypatch):
    monkeypatch.setattr(fixer, "get_incident", lambda _id: {"triage": {}})
    monkeypatch.setattr(
        fixer, "open_pr",
        lambda **_kwargs: {"pr_url": "https://example.invalid/pr/1", "branch": "b", "head_sha": "h"},
    )
    monkeypatch.setattr(fixer, "update_incident", lambda *_a, **_kw: None)
    ci = Mock(side_effect=[
        {"status": "failure", "failure_details": "bad"},
        {"status": "failure", "failure_details": "still bad"},
    ])
    monkeypatch.setattr(fixer, "wait_for_ci_status", ci)
    monkeypatch.setattr(fixer, "get_llm", lambda: RunnableLambda(lambda value: value))
    monkeypatch.setattr(fixer, "prompt", RunnableLambda(lambda value: value))
    monkeypatch.setattr(fixer, "invoke_guarded", lambda *_a, **_kw: SimpleNamespace(
        hunks=[{"old_text": "return 1", "new_text": "return 2"}], commit_message="fix"
    ))
    monkeypatch.setattr(fixer, "update_branch_file", lambda *_a, **_kw: "retry-sha")
    solution = {"title": "fix", "risk": "low"}
    fixer.run_push("incident", {"root_cause": "cause", "permanent_fix": solution}, "permanent", {
        "path": "app.py", "new_content": "def f():\n    return 1\n", "commit_message": "fix", "base_sha": "base",
    })
    assert ci.call_count == 2
