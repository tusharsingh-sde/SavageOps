"""Central LLM chain management, multi-provider failover, rate limiting, and telemetry."""

import contextvars
import logging
import os
import threading
import time
from collections import deque
from datetime import UTC, datetime
from typing import Any

from langchain_core.messages import BaseMessage
from langchain_core.runnables import Runnable
from langchain_openai import ChatOpenAI
from pydantic import BaseModel

from savageops.config import MAX_DAILY_LLM_CALLS, MAX_DAILY_LLM_TOKENS

logger = logging.getLogger(__name__)

# Context variable to correlate LLM calls with incident IDs
_current_incident_id: contextvars.ContextVar[str] = contextvars.ContextVar(
    "_current_incident_id", default=""
)

# Supported OpenAI-compatible providers
PROVIDERS: dict[str, dict[str, Any]] = {
    "gemini": {
        "base_url": "https://generativelanguage.googleapis.com/v1beta/openai/",
        "env_key": "GEMINI_API_KEY",
        "default_tpm": 1_000_000,
        "default_rpm": 60,
    },
    "mistral": {
        "base_url": "https://api.mistral.ai/v1",
        "env_key": "MISTRAL_API_KEY",
        "default_tpm": 500_000,
        "default_rpm": 60,
    },
    "groq": {
        "base_url": "https://api.groq.com/openai/v1",
        "env_key": "GROQ_API_KEY",
        "default_tpm": 8000,  # Requirement: Groq 8000 TPM
        "default_rpm": 30,
    },
}

DEFAULT_CHAINS: dict[str, str] = {
    "triage": "gemini:gemini-3.5-flash-lite,mistral:mistral-small-latest",
    "analyst": "gemini:gemini-3.8-flash,mistral:mistral-large-latest",
    "fixer": "mistral:devstral-2-2512,gemini:gemini-3.8-flash",
    "spokesperson": "groq:openai/gpt-oss-120b,gemini:gemini-3.5-flash-lite",
}

REJECT_TEMPERATURE_PREFIXES = ("o1", "o3", "o4", "gpt-5", "devstral")


def can_pass_temperature(model: str) -> bool:
    """Return True if model accepts a temperature parameter."""
    m = model.lower()
    return not any(m.startswith(p) for p in REJECT_TEMPERATURE_PREFIXES)


def estimate_tokens(prompt_input: Any) -> int:
    """Heuristic token estimator for prompt inputs."""
    if isinstance(prompt_input, str):
        text = prompt_input
    elif isinstance(prompt_input, list):
        text = " ".join(
            m.content if hasattr(m, "content") else str(m) for m in prompt_input
        )
    elif hasattr(prompt_input, "to_messages"):
        messages = prompt_input.to_messages()
        text = " ".join(
            m.content if hasattr(m, "content") else str(m) for m in messages
        )
    elif isinstance(prompt_input, dict):
        text = str(prompt_input)
    else:
        text = str(prompt_input)
    return max(1, len(text) // 4 + 50)


class ProviderRateLimiter:
    """Sliding-window rate limiter for tracking RPM and TPM per provider."""

    def __init__(self, provider: str, tpm: int, rpm: int):
        self.provider = provider
        self.tpm = tpm
        self.rpm = rpm
        self.lock = threading.Lock()
        self.history: deque[tuple[float, int]] = deque()
        self.throttled_until: float = 0.0

    def _prune(self, now: float) -> None:
        cutoff = now - 60.0
        while self.history and self.history[0][0] <= cutoff:
            self.history.popleft()

    def can_accept(self, estimated_tokens: int) -> bool:
        """Prompt-size guard: returns False if prompt exceeds TPM or remaining capacity."""
        if estimated_tokens > self.tpm:
            return False
        with self.lock:
            now = time.monotonic()
            if now < self.throttled_until:
                return False
            self._prune(now)
            current_tokens = sum(tokens for _, tokens in self.history)
            current_calls = len(self.history)
            if current_tokens + estimated_tokens > self.tpm:
                return False
            if current_calls + 1 > self.rpm:
                return False
            return True

    def record_usage(self, actual_tokens: int) -> None:
        with self.lock:
            now = time.monotonic()
            self._prune(now)
            self.history.append((now, actual_tokens))

    def throttle(self, seconds: float) -> None:
        with self.lock:
            self.throttled_until = max(self.throttled_until, time.monotonic() + seconds)

    def reset(self) -> None:
        with self.lock:
            self.history.clear()
            self.throttled_until = 0.0


# Global provider rate limiters
_rate_limiters: dict[str, ProviderRateLimiter] = {
    p: ProviderRateLimiter(
        p,
        int(os.getenv(f"{p.upper()}_TPM", str(cfg["default_tpm"]))),
        int(os.getenv(f"{p.upper()}_RPM", str(cfg["default_rpm"]))),
    )
    for p, cfg in PROVIDERS.items()
}


def get_limiter(provider: str) -> ProviderRateLimiter:
    tpm_env = os.getenv(f"{provider.upper()}_TPM")
    rpm_env = os.getenv(f"{provider.upper()}_RPM")
    limiter = _rate_limiters.get(provider)
    if limiter is None:
        tpm = (
            int(tpm_env)
            if tpm_env
            else PROVIDERS.get(provider, {}).get("default_tpm", 500_000)
        )
        rpm = (
            int(rpm_env)
            if rpm_env
            else PROVIDERS.get(provider, {}).get("default_rpm", 60)
        )
        limiter = ProviderRateLimiter(provider, tpm, rpm)
        _rate_limiters[provider] = limiter
    else:
        if tpm_env:
            limiter.tpm = int(tpm_env)
        if rpm_env:
            limiter.rpm = int(rpm_env)
    return limiter


def parse_chain(role: str) -> list[tuple[str, str]]:
    """Parse comma-separated provider:model string for role.

    Raises clear error naming only the variable if unknown provider or missing key.
    """
    env_var_name = f"LLM_{role.upper()}"
    raw = os.getenv(env_var_name, "").strip() or DEFAULT_CHAINS.get(role.lower(), "")
    if not raw:
        raise RuntimeError(f"Missing configuration: {env_var_name}")

    entries: list[tuple[str, str]] = []
    for item in raw.split(","):
        item = item.strip()
        if not item:
            continue
        if ":" not in item:
            raise RuntimeError(
                f"Invalid chain format in {env_var_name}: '{item}' (expected provider:model)"
            )
        prov, model = item.split(":", 1)
        prov = prov.strip().lower()
        model = model.strip()
        if prov not in PROVIDERS:
            raise RuntimeError(f"Unknown LLM provider '{prov}' in {env_var_name}")
        # Verify provider API key exists
        key_var = PROVIDERS[prov]["env_key"]
        key_val = os.getenv(key_var, "").strip()
        if not key_val:
            raise RuntimeError(f"Missing required environment variable: {key_var}")
        entries.append((prov, model))

    if not entries:
        raise RuntimeError(f"No valid model chain configured in {env_var_name}")
    return entries


def _parse_retry_after(error: Exception) -> float | None:
    """Extract Retry-After value from HTTP response headers if present."""
    response = getattr(error, "response", None)
    if response is not None and hasattr(response, "headers"):
        val = response.headers.get("retry-after")
        if val:
            try:
                return float(val)
            except ValueError:
                pass
    return None


def _is_rate_limit_or_transient(error: Exception) -> bool:
    """Identify HTTP 429, 5xx, or network timeouts."""
    status_code = getattr(error, "status_code", None)
    if status_code is None:
        response = getattr(error, "response", None)
        if response is not None:
            status_code = getattr(response, "status_code", None)
    if status_code in (429, 500, 502, 503, 504):
        return True
    msg = str(error).lower()
    return "rate limit" in msg or "429" in msg or "timeout" in msg or "timed out" in msg


class RoleChainRunner(Runnable):
    """Runnable executing ordered chain failover, rate limiting, and telemetry."""

    def __init__(
        self,
        role: str,
        schema: type[BaseModel] | None = None,
        temperature: float | None = None,
    ):
        self.role = role
        self.schema = schema
        self.temperature = temperature

    def with_structured_output(self, schema: type[BaseModel]) -> "RoleChainRunner":
        """Return a copy configured to produce structured output using schema."""
        return RoleChainRunner(
            role=self.role,
            schema=schema,
            temperature=self.temperature,
        )

    def invoke(self, input_val: Any, config: Any = None, **kwargs: Any) -> Any:
        chain = parse_chain(self.role)
        estimated_tokens = estimate_tokens(input_val)
        incident_id = _current_incident_id.get() or "system"

        errors: list[str] = []
        for depth, (provider, model) in enumerate(chain):
            limiter = get_limiter(provider)
            # Prompt-size guard: check if this model can accept the prompt without exceeding TPM
            if not limiter.can_accept(estimated_tokens):
                logger.warning(
                    "Prompt-size guard: prompt (%d tokens) exceeds remaining TPM for provider '%s'; preferring next chain entry",
                    estimated_tokens,
                    provider,
                )
                errors.append(
                    f"{provider}:{model} skipped (prompt {estimated_tokens} tokens exceeds TPM limit {limiter.tpm})"
                )
                continue

            # Instantiate client
            prov_cfg = PROVIDERS[provider]
            api_key = os.getenv(prov_cfg["env_key"], "")
            client_kwargs: dict[str, Any] = {
                "model": model,
                "api_key": api_key,
                "base_url": prov_cfg["base_url"],
                "timeout": 60,
                "max_retries": 0,
            }
            if self.temperature is not None and can_pass_temperature(model):
                client_kwargs["temperature"] = self.temperature

            client = ChatOpenAI(**client_kwargs)

            # Try execution with structured output fallback methods or standard call
            methods = (
                ["default", "function_calling", "json_mode"]
                if self.schema is not None
                else [None]
            )
            success = False
            result = None
            last_err = None

            start_time = time.monotonic()
            for method in methods:
                try:
                    if self.schema is not None:
                        if method == "default":
                            so_llm = client.with_structured_output(
                                self.schema, include_raw=True
                            )
                        else:
                            so_llm = client.with_structured_output(
                                self.schema, method=method, include_raw=True
                            )
                        raw_out = so_llm.invoke(input_val)
                        if isinstance(raw_out, dict) and raw_out.get("parsing_error"):
                            raise RuntimeError(
                                f"Structured output parsing failed with method {method}: {raw_out['parsing_error']}"
                            )
                        result = (
                            raw_out.get("parsed")
                            if isinstance(raw_out, dict)
                            else raw_out
                        )
                        raw_msg = (
                            raw_out.get("raw")
                            if isinstance(raw_out, dict)
                            else None
                        )
                    else:
                        result = client.invoke(input_val)
                        raw_msg = (
                            result if isinstance(result, BaseMessage) else None
                        )

                    success = True
                    break
                except Exception as ex:
                    last_err = ex
                    # If this was an HTTP rate limit or 5xx or timeout, don't keep retrying methods for this dead provider
                    if _is_rate_limit_or_transient(ex):
                        break
                    logger.info(
                        "Structured output method '%s' failed for %s:%s: %s; trying next method",
                        method,
                        provider,
                        model,
                        ex,
                    )

            latency = time.monotonic() - start_time

            if success:
                # Extract token usage
                in_tokens = estimated_tokens
                out_tokens = 0
                if raw_msg is not None:
                    metadata = getattr(raw_msg, "response_metadata", {}) or {}
                    token_usage = metadata.get("token_usage", {}) or {}
                    in_tokens = token_usage.get("prompt_tokens", in_tokens)
                    out_tokens = token_usage.get(
                        "completion_tokens", estimate_tokens(result)
                    )
                limiter.record_usage(in_tokens + out_tokens)

                # Record Telemetry to Prometheus & agent_runs
                _record_telemetry(
                    incident_id=incident_id,
                    role=self.role,
                    provider=provider,
                    model=model,
                    input_tokens=in_tokens,
                    output_tokens=out_tokens,
                    latency=latency,
                    fallback_depth=depth,
                    error=None,
                )
                return result

            # Failure path for this chain entry
            retry_after = _parse_retry_after(last_err)
            if retry_after is not None:
                limiter.throttle(retry_after)

            err_msg = str(last_err) if last_err else "unknown error"
            errors.append(f"{provider}:{model} depth={depth} failed: {err_msg}")
            logger.warning(
                "Chain failover: %s:%s (depth %d) failed (%s); advancing to next entry",
                provider,
                model,
                depth,
                err_msg,
            )

            # Record failed attempt telemetry
            _record_telemetry(
                incident_id=incident_id,
                role=self.role,
                provider=provider,
                model=model,
                input_tokens=estimated_tokens,
                output_tokens=0,
                latency=latency,
                fallback_depth=depth,
                error=err_msg,
            )

        # Whole chain failed: fail incident with clear reason, never hang
        raise RuntimeError(
            f"All LLM providers in chain failed for role '{self.role}': {'; '.join(errors)}"
        )


def _record_telemetry(
    incident_id: str,
    role: str,
    provider: str,
    model: str,
    input_tokens: int,
    output_tokens: int,
    latency: float,
    fallback_depth: int,
    error: str | None,
) -> None:
    """Record metrics to Prometheus and persist call details into agent_runs."""
    try:
        from savageops.metrics import (
            LLM_CALLS,
            LLM_FALLBACK_DEPTH,
            LLM_LATENCY,
            LLM_TOKENS,
        )

        status = "error" if error else "success"
        LLM_CALLS.labels(
            provider=provider, model=model, role=role, status=status
        ).inc()
        LLM_TOKENS.labels(
            provider=provider, model=model, role=role, type="input"
        ).inc(input_tokens)
        LLM_TOKENS.labels(
            provider=provider, model=model, role=role, type="output"
        ).inc(output_tokens)
        LLM_LATENCY.labels(provider=provider, model=model, role=role).observe(
            latency
        )
        LLM_FALLBACK_DEPTH.labels(role=role).observe(fallback_depth)
    except Exception as me:
        logger.debug("Failed to record LLM metrics: %s", me)

    try:
        from savageops.db import log_run

        log_run(
            incident_id=incident_id,
            agent=role,
            kind="llm_call",
            provider=provider,
            model=model,
            role=role,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            latency=latency,
            fallback_depth=fallback_depth,
            error=error,
        )
    except Exception as de:
        logger.debug("Failed to log LLM call to agent_runs: %s", de)


def get_llm(
    role: str,
    schema: type[BaseModel] | None = None,
    temperature: float | None = None,
) -> RoleChainRunner:
    """Get the primary LLM chain runner for the specified agent role."""
    return RoleChainRunner(role=role, schema=schema, temperature=temperature)


# Daily request cap state
_budget_lock = threading.Lock()
_usage_date = datetime.now(UTC).date()
_calls_today = 0
_tokens_reserved_today = 0


def invoke_guarded(runnable: Any, inputs: dict, incident_id: str) -> Any:
    """Enforce daily budget ceiling, set incident context, and invoke model chain."""
    global _usage_date, _calls_today, _tokens_reserved_today
    today = datetime.now(UTC).date()
    estimated_tokens = estimate_tokens(inputs)

    with _budget_lock:
        if today != _usage_date:
            _usage_date = today
            _calls_today = 0
            _tokens_reserved_today = 0
        if _calls_today >= MAX_DAILY_LLM_CALLS:
            raise RuntimeError(
                f"Daily LLM request cap ({MAX_DAILY_LLM_CALLS}) reached; incident {incident_id} needs human review"
            )
        if _tokens_reserved_today + estimated_tokens > MAX_DAILY_LLM_TOKENS:
            raise RuntimeError(
                f"Daily LLM token budget ({MAX_DAILY_LLM_TOKENS}) reached; incident {incident_id} needs human review"
            )
        _calls_today += 1
        _tokens_reserved_today += estimated_tokens

    token = _current_incident_id.set(incident_id)
    try:
        return runnable.invoke(inputs)
    finally:
        _current_incident_id.reset(token)


def list_provider_models() -> dict[str, list[str]]:
    """List model IDs returned by each configured provider (names only)."""
    from openai import OpenAI

    results: dict[str, list[str]] = {}
    for prov, cfg in PROVIDERS.items():
        key = os.getenv(cfg["env_key"], "").strip()
        if not key:
            results[prov] = []
            continue
        try:
            client = OpenAI(api_key=key, base_url=cfg["base_url"], timeout=15)
            models_resp = client.models.list()
            ids = sorted([str(m.id) for m in models_resp.data])
            results[prov] = ids
        except Exception as e:
            logger.warning("Failed to fetch models from provider '%s': %s", prov, e)
            results[prov] = []
    return results


def check_chain_models() -> list[dict[str, Any]]:
    """Verify configured chain entries against model lists and flag missing entries."""
    available_models = list_provider_models()
    checks: list[dict[str, Any]] = []

    for role in ("triage", "analyst", "fixer", "spokesperson"):
        try:
            chain = parse_chain(role)
        except Exception as e:
            checks.append(
                {"role": role, "entry": "none", "status": f"error: {e}"}
            )
            continue

        for prov, model in chain:
            models_for_prov = available_models.get(prov, [])
            key = os.getenv(PROVIDERS[prov]["env_key"], "")
            if not key:
                status = "skipped (no key)"
            elif not models_for_prov:
                status = "unverified (could not list models)"
            elif model in models_for_prov:
                status = "ok"
            else:
                status = f"FLAG: '{model}' not in provider model list"

            checks.append(
                {"role": role, "entry": f"{prov}:{model}", "status": status}
            )
    return checks
