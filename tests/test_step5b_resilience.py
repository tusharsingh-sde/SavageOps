"""Tests for Step 5B hardening: config, auth, health, and main lifecycle resilience."""

from threading import Lock
from unittest.mock import MagicMock, patch

import jwt
import pytest
from fastapi import HTTPException

from savageops import auth, config, health, main


def test_auth_bearer_parsing_and_case_insensitivity(monkeypatch):
    """'bearer <token>' (lowercase) succeeds; raw token without 'Bearer' raises 401."""
    secret = "test-jwt-secret-for-step5b-at-least-32-bytes-long!"
    admin_id = "admin-user-step5b"
    monkeypatch.setattr(config, "SUPABASE_JWT_SECRET", secret)
    monkeypatch.setattr(config, "ADMIN_USER_IDS", [admin_id])
    monkeypatch.setattr(auth, "SUPABASE_JWT_SECRET", secret)
    monkeypatch.setattr(auth, "ADMIN_USER_IDS", [admin_id])

    valid_token = jwt.encode({"sub": admin_id, "aud": "authenticated"}, secret, algorithm="HS256")

    # 1. Lowercase "bearer <token>" should succeed
    claims = auth.require_admin(f"bearer {valid_token}")
    assert claims["sub"] == admin_id

    # 2. Raw token without "Bearer " prefix should raise 401
    with pytest.raises(HTTPException) as exc_raw:
        auth.require_admin(valid_token)
    assert exc_raw.value.status_code == 401
    assert "Bearer" in exc_raw.value.detail


def test_config_defaults_have_no_hallucinated_gemini3_strings():
    """Default LLM chains must not reference non-existent gemini-3.x models."""
    for chain_name in ("LLM_TRIAGE", "LLM_ANALYST", "LLM_FIXER", "LLM_SPOKESPERSON"):
        val = getattr(config, chain_name, "")
        assert "gemini-3." not in val, f"{chain_name} contains unverified gemini-3.*: {val}"


def test_health_grafana_docker_url_and_failure_ttl(monkeypatch):
    """http://grafana:3000 without token should probe endpoint, and failure cache TTL should be 5s."""
    health._checks_cache = None
    health._checks_cached_at = 0.0

    monkeypatch.setattr(health, "GRAFANA_URL", "http://grafana:3000")
    monkeypatch.setattr(health, "GRAFANA_TOKEN", "")

    # In Docker compose, http://grafana:3000 should not be skipped to fixture_mode
    with patch("httpx.get") as mock_get:
        mock_resp = MagicMock()
        mock_resp.raise_for_status.side_effect = RuntimeError("grafana connection refused")
        mock_get.return_value = mock_resp

        res = health.run_checks(ping_llms=False)
        assert res["grafana"].startswith("fail:")
        mock_get.assert_called()

        # Cache TTL for failed checks should be 5s (not 60s)
        assert getattr(health, "_checks_cache_ttl", None) == 5.0


def test_main_startup_recovery_only_recovers_received_status(monkeypatch):
    """Startup recovery must only query status='received', never 'triaged'."""
    mock_db = MagicMock()
    mock_table = MagicMock()
    mock_db.table.return_value = mock_table
    mock_table.select.return_value = mock_table
    mock_table.eq.return_value = mock_table
    mock_table.in_.return_value = mock_table
    mock_table.execute.return_value = MagicMock(data=[])

    monkeypatch.setattr(main, "db", mock_db)
    monkeypatch.setattr(main, "validate_startup_config", lambda: None)
    monkeypatch.setattr(main, "WEBHOOK_SECRET", "test-secret")
    monkeypatch.setattr(main, "DATABASE_URL", "")
    monkeypatch.setattr(main, "POLL_ENABLED", False)

    import asyncio

    async def _run_lifespan():
        async with main.lifespan(main.app):
            pass

    asyncio.run(_run_lifespan())

    # Ensure eq('status', 'received') was called, not in_('status', ['received', 'triaged'])
    mock_table.eq.assert_called_with("status", "received")
    mock_table.in_.assert_not_called()


def test_main_incident_locks_pruning():
    """_get_incident_lock prunes unlocked entries when entries exceed 1000."""
    main._incident_locks.clear()

    # Pre-populate 1050 unlocked entries
    for i in range(1050):
        main._incident_locks[f"stale-{i}"] = Lock()

    lock = main._get_incident_lock("new-incident")
    assert hasattr(lock, "acquire") and hasattr(lock, "release")
    # Stale entries should have been pruned
    assert len(main._incident_locks) < 1000
