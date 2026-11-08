"""Health check service and endpoints for SavageOps and CLI doctor command."""

import time

import httpx
from fastapi import APIRouter, Depends

from savageops.auth import require_admin
from savageops.config import (
    GRAFANA_TOKEN,
    GRAFANA_URL,
    LOKI_DATASOURCE_UID,
    LOKI_URL,
    POLL_ENABLED,
    POLL_INTERVAL_SECONDS,
)
from savageops.db import db
from savageops.llm import PROVIDERS
from savageops.sentinel.poller import state as poller_state
from savageops.tools.github_tools import _get_client_and_repo

router = APIRouter(tags=["health"])


def run_checks() -> dict[str, str]:
    """Execute full diagnostic checks against all external dependencies and subsystems."""
    global _checks_cache, _checks_cached_at
    now = time.monotonic()
    if _checks_cache is not None and now - _checks_cached_at < 60:
        return dict(_checks_cache)
    checks: dict[str, str] = {}
    auth_header = {"Authorization": f"Bearer {GRAFANA_TOKEN}"} if GRAFANA_TOKEN else {}

    def check(name: str, fn):
        try:
            fn()
            checks[name] = "ok"
        except Exception as e:
            checks[name] = f"fail: {str(e)[:120]}"

    # 1. Supabase check
    check("supabase", lambda: db.table("incidents").select("id").limit(1).execute())

    # 2. GitHub check
    def _check_github():
        _, repo = _get_client_and_repo()
        if not repo.default_branch:
            raise ValueError("No default branch returned")

    check("github", _check_github)

    # 3. LLM provider checks (pings configured providers with max_tokens=5)
    default_ping_models = {
        "gemini": "gemini-3.5-flash-lite",
        "mistral": "mistral-small-latest",
        "groq": "openai/gpt-oss-120b",
    }
    import os

    for prov, cfg in PROVIDERS.items():
        key_var = cfg["env_key"]
        api_key = os.getenv(key_var, "").strip()
        if not api_key:
            checks[f"llm_{prov}"] = f"not_configured: {key_var} not set"
        else:
            def _make_ping(p=prov, c=cfg, k=api_key):
                def _do_ping():
                    from openai import OpenAI

                    client = OpenAI(api_key=k, base_url=c["base_url"], timeout=10)
                    model = default_ping_models.get(p, "default")
                    client.chat.completions.create(
                        model=model,
                        messages=[{"role": "user", "content": "ping"}],
                        max_tokens=5,
                    )
                return _do_ping

            check(f"llm_{prov}", _make_ping())

    # 4. Grafana check
    if GRAFANA_URL and "localhost" not in GRAFANA_URL and not GRAFANA_TOKEN:
        checks["grafana"] = "fixture_mode"
    else:

        def _check_grafana():
            r = httpx.get(f"{GRAFANA_URL.rstrip('/')}/api/health", timeout=5)
            r.raise_for_status()

        check("grafana", _check_grafana)

    # 5. Loki check
    if not LOKI_URL and not GRAFANA_TOKEN:
        checks["loki"] = "fixture_mode"
    else:

        def _check_loki():
            if LOKI_URL:
                r = httpx.get(f"{LOKI_URL.rstrip('/')}/ready", timeout=5)
                r.raise_for_status()
            else:
                r = httpx.get(
                    f"{GRAFANA_URL.rstrip('/')}/api/datasources/uid/{LOKI_DATASOURCE_UID}/health",
                    headers=auth_header,
                    timeout=8,
                )
                r.raise_for_status()

        check("loki", _check_loki)

    # 6. Poller Heartbeat check
    if POLL_ENABLED:
        last = poller_state.get("last_ok")
        if last is not None and (time.time() - last) < (3 * POLL_INTERVAL_SECONDS):
            checks["poller"] = "ok"
        elif poller_state.get("fails", 0) > 0:
            checks["poller"] = f"fail: poller failed {poller_state.get('fails')} times"
        else:
            checks["poller"] = "starting"

    _checks_cache = checks
    _checks_cached_at = now
    return dict(checks)


_checks_cache: dict[str, str] | None = None
_checks_cached_at = 0.0


@router.get("/health")
def health() -> dict[str, str]:
    """Lightweight liveness probe."""
    return {"status": "ok"}


@router.get("/health/deep")
def deep_health(admin: dict = Depends(require_admin)) -> dict:
    """Comprehensive dependency and subsystem readiness probe."""
    checks = run_checks()
    all_ok = all(v in ("ok", "fixture_mode", "starting") for v in checks.values())
    return {"ok": all_ok, "checks": checks}
