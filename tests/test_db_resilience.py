"""Tests for Supabase database resilience, thread-safety, and log_run guards."""

from unittest.mock import MagicMock

from savageops import db as db_module


def test_get_incident_returns_none_when_empty_without_single(monkeypatch):
    """get_incident uses limit(1) and returns None without raising PGRST116 (.single())."""
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("SAVAGEOPS_MOCK", "0")
    monkeypatch.setattr(db_module, "APP_ENV", "production", raising=False)

    mock_client = MagicMock()
    mock_query = MagicMock()
    mock_client.table.return_value = mock_query
    mock_query.select.return_value = mock_query
    mock_query.eq.return_value = mock_query
    mock_query.limit.return_value = mock_query
    # When limit(1).execute() returns empty data
    mock_res = MagicMock()
    mock_res.data = []
    mock_query.execute.return_value = mock_res

    monkeypatch.setattr(db_module, "_client", mock_client)

    result = db_module.get_incident("missing-incident-id")
    assert result is None
    # Ensure .single() is not called
    mock_query.single.assert_not_called()
    mock_query.limit.assert_called_once_with(1)


def test_get_incident_system_returns_none_without_querying(monkeypatch):
    """get_incident('system') immediately returns None without hitting DB."""
    mock_client = MagicMock()
    monkeypatch.setattr(db_module, "_client", mock_client)
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("SAVAGEOPS_MOCK", "0")
    monkeypatch.setattr(db_module, "APP_ENV", "production", raising=False)

    result = db_module.get_incident("system")
    assert result is None
    mock_client.table.assert_not_called()


def test_log_run_system_skips_db_insert_but_increments_metric(monkeypatch):
    """log_run('system') increments AGENT_RUNS but skips agent_runs DB insert."""
    from savageops.metrics import AGENT_RUNS

    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("SAVAGEOPS_MOCK", "0")
    monkeypatch.setattr(db_module, "APP_ENV", "production", raising=False)

    mock_client = MagicMock()
    monkeypatch.setattr(db_module, "_client", mock_client)

    init_val = AGENT_RUNS.labels(agent="triage", kind="call")._value.get()

    db_module.log_run("system", "triage", "call", inp={"test": 1})

    new_val = AGENT_RUNS.labels(agent="triage", kind="call")._value.get()
    assert new_val == init_val + 1
    mock_client.table.assert_not_called()


def test_log_run_canary_cache_queries_db_only_once(monkeypatch):
    """Repeated log_run calls query incidents table only once for canary status."""
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("SAVAGEOPS_MOCK", "0")
    monkeypatch.setattr(db_module, "APP_ENV", "production", raising=False)

    # Clear cache before test
    if hasattr(db_module, "_canary_cache"):
        db_module._canary_cache.clear()

    mock_client = MagicMock()
    mock_incidents_query = MagicMock()
    mock_runs_query = MagicMock()

    def table_router(table_name):
        if table_name == "incidents":
            return mock_incidents_query
        return mock_runs_query

    mock_client.table.side_effect = table_router
    mock_incidents_query.select.return_value = mock_incidents_query
    mock_incidents_query.eq.return_value = mock_incidents_query
    mock_incidents_query.limit.return_value = mock_incidents_query
    mock_inc_res = MagicMock()
    mock_inc_res.data = [{"source": "webhook"}]
    mock_incidents_query.execute.return_value = mock_inc_res

    mock_runs_query.insert.return_value = mock_runs_query
    mock_runs_query.execute.return_value = MagicMock()

    monkeypatch.setattr(db_module, "_client", mock_client)

    test_id = "incident-cached-test-123"
    for _ in range(5):
        db_module.log_run(test_id, "triage", "call")

    # Incidents table should be queried exactly once
    assert mock_incidents_query.execute.call_count == 1
    # agent_runs table should be inserted 5 times
    assert mock_runs_query.insert.call_count == 5
