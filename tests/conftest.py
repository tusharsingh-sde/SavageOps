"""Test configuration, autouse isolation fixtures, and multi-layer network blocker."""

import os
import socket
from contextlib import ExitStack
from typing import Any
from unittest.mock import patch

import pytest

# Enforce test environment flags and disable python-dotenv from reading local .env
os.environ["APP_ENV"] = "test"
os.environ["SAVAGEOPS_MOCK"] = "1"
os.environ["PYTHON_DOTENV_DISABLED"] = "1"
os.environ["POLL_ENABLED"] = "false"

# Safe default service configurations and secrets for test isolation
os.environ["WEBHOOK_SECRET"] = "test-webhook-secret"
os.environ["METRICS_TOKEN"] = "test-metrics-token"
os.environ["SUPABASE_URL"] = "http://127.0.0.1:9"
os.environ["GRAFANA_URL"] = "http://127.0.0.1:9"
os.environ["LOKI_URL"] = "http://127.0.0.1:9"
os.environ["DATABASE_URL"] = ""
os.environ["SLACK_WEBHOOK_URL"] = ""
os.environ["CI_NO_CHECKS_GRACE_SECONDS"] = "0"

# Strip real secret keys from environment during unit tests to ensure test isolation
for _sec_var in (
    "GEMINI_API_KEY",
    "MISTRAL_API_KEY",
    "GROQ_API_KEY",
    "OPENROUTER_API_KEY",
    "GITHUB_TOKEN",
    "SUPABASE_SERVICE_KEY",
    "SUPABASE_JWT_SECRET",
    "GRAFANA_TOKEN",
    "GRAFANA_ADMIN_PASSWORD",
):
    os.environ.pop(_sec_var, None)

# Clear non-secret LLM chain/temperature vars so default chains apply cleanly during tests
for _llm_var in (
    "LLM_TRIAGE",
    "LLM_ANALYST",
    "LLM_FIXER",
    "LLM_SPOKESPERSON",
    "LLM_TEMPERATURE_TRIAGE",
    "LLM_TEMPERATURE_ANALYST",
    "LLM_TEMPERATURE_FIXER",
    "LLM_TEMPERATURE_SPOKESPERSON",
    "LLM_TEMP_GEMINI",
    "LLM_ALLOW_PAID",
):
    os.environ.pop(_llm_var, None)


def _is_loopback(host: Any) -> bool:
    h = str(host).lower().strip()
    return h in ("127.0.0.1", "::1", "localhost")


@pytest.fixture(autouse=True)
def block_external_network(request):
    """Multi-layer network blocker.

    1. Socket-level: blocks raw socket connects to non-loopback hosts and blocks DNS getaddrinfo.
       (Loopback connect is permitted at socket level specifically for Windows asyncio ProactorEventLoop self-pipe).
    2. Library-level: blocks all HTTP/DB clients (httpx sync/async, requests, urllib3, psycopg if installed) for ALL hosts, including 127.0.0.1.
    """
    if "allow_network" in request.keywords:
        yield
        return

    orig_connect = socket.socket.connect
    orig_getaddrinfo = socket.getaddrinfo

    def blocked_connect(self, address):
        host = address[0] if isinstance(address, (tuple, list)) else address
        if not _is_loopback(host):
            raise RuntimeError(f"Network access blocked in tests: raw socket connect to {host}")
        return orig_connect(self, address)

    def blocked_getaddrinfo(host, port, *args, **kwargs):
        if not _is_loopback(host):
            raise RuntimeError(f"Network access blocked in tests: DNS lookup for {host}")
        return orig_getaddrinfo(host, port, *args, **kwargs)

    def blocked_httpx_send(self, request, *args, **kwargs):
        url = getattr(request, "url", "unknown")
        raise RuntimeError(f"Network access blocked in tests: httpx request to {url}")

    async def blocked_httpx_async_send(self, request, *args, **kwargs):
        url = getattr(request, "url", "unknown")
        raise RuntimeError(f"Network access blocked in tests: httpx async request to {url}")

    def blocked_requests_send(self, request, *args, **kwargs):
        url = getattr(request, "url", "unknown")
        raise RuntimeError(f"Network access blocked in tests: requests call to {url}")

    def blocked_urllib3_urlopen(self, method, url, *args, **kwargs):
        raise RuntimeError(f"Network access blocked in tests: urllib3 request to {url}")

    stack = ExitStack()
    stack.enter_context(patch.object(socket.socket, "connect", blocked_connect))
    stack.enter_context(patch.object(socket, "getaddrinfo", blocked_getaddrinfo))
    stack.enter_context(patch("httpx.Client.send", blocked_httpx_send))
    stack.enter_context(patch("httpx.AsyncClient.send", blocked_httpx_async_send))
    stack.enter_context(patch("requests.Session.send", blocked_requests_send))
    stack.enter_context(patch("urllib3.connectionpool.HTTPConnectionPool.urlopen", blocked_urllib3_urlopen))

    try:
        import psycopg
        def blocked_psycopg_connect(*args, **kwargs):
            raise RuntimeError("Network access blocked in tests: psycopg database connection")
        stack.enter_context(patch.object(psycopg, "connect", blocked_psycopg_connect))
    except ImportError:
        pass

    with stack:
        yield


@pytest.fixture(autouse=True)
def isolate_in_memory_db():
    """Ensure in-memory mock database state is completely reset and isolated."""
    import savageops.db as db_module

    db_module._memory_incidents.clear()
    db_module._memory_agent_runs.clear()
    yield
    db_module._memory_incidents.clear()
    db_module._memory_agent_runs.clear()
