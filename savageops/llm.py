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
        "default_rpd": None,
    },
    "mistral": {
        "base_url": "https://api.mistral.ai/v1",
        "env_key": "MISTRAL_API_KEY",
        "default_tpm": 500_000,
        "default_rpm": 60,
        "default_rpd": None,
    },
    "groq": {
        "base_url": "https://api.groq.com/openai/v1",
        "env_key": "GROQ_API_KEY",
        "default_tpm": 8000,  # Requirement: Groq 8000 TPM
        "default_rpm": 30,
        "default_rpd": None,
    },
    "openrouter": {
        "base_url": "https://openrouter.ai/api/v1",
        "env_key": "OPENROUTER_API_KEY",
        "default_tpm": 1_000_000,
        "default_rpm": 20,  # Requirement: OpenRouter 20 RPM
        "default_rpd": 50,  # Requirement: OpenRouter per-process 50 req/day cap
    },
}

DEFAULT_CHAINS: dict[str, str] = {
    "triage": "gemini:gemini-2.5-flash-lite,gemini:gemini-2.5-flash",
    "analyst": "gemini:gemini-2.5-flash,openrouter:openai/gpt-oss-120b:free",
    "fixer": "openrouter:qwen/qwen3-coder:free,gemini:gemini-2.5-flash",
    "spokesperson": "groq:openai/gpt-oss-120b,gemini:gemini-2.5-flash-lite",
}

DEFAULT_TEMPERATURES: dict[str, float] = {
    "fixer": 0.1,
    "triage": 0.1,
    "analyst": 0.2,
    "spokesperson": 0.7,
}

REJECT_TEMPERATURE_PREFIXES = ("o1", "o3", "o4", "gpt-5")

_temperature_rejected_lock = threading.Lock()
_temperature_rejected_models: set[str] = set()


def _remember_temperature_rejected(model: str) -> None:
    with _temperature_rejected_lock:
        _temperature_rejected_models.add(model.lower())


def reset_temperature_rejections() -> None:
    """Clear process-local cache of models that rejected temperature."""
    with _temperature_rejected_lock:
        _temperature_rejected_models.clear()


def can_pass_temperature(model: str) -> bool:
    """Return True if model accepts a temperature parameter."""
    m = model.lower()
    if any(m.startswith(p) for p in REJECT_TEMPERATURE_PREFIXES):
        return False
    with _temperature_rejected_lock:
        return m not in _temperature_rejected_models


def resolve_temperature(
    role: str,
    provider: str,
    model: str,
    explicit_temperature: float | None = None,
) -> float | None:
    """Resolve effective temperature for role/provider/model, or None to omit."""
    if not can_pass_temperature(model):
        return None

    env_name = f"LLM_TEMPERATURE_{role.upper()}"
    env_val = os.getenv(env_name)
    if env_val is not None:
        stripped = env_val.strip()
        if not stripped or stripped.lower() in ("none", "default", "unset", "off"):
            role_temp: float | None = None
        else:
            try:
                role_temp = float(stripped)
            except ValueError as e:
                raise RuntimeError(f"Invalid float value in {env_name}") from e
    elif explicit_temperature is not None:
        role_temp = explicit_temperature
    else:
        role_temp = DEFAULT_TEMPERATURES.get(role.lower())

    # Gemini defaults to provider default (no temperature) unless LLM_TEMP_GEMINI is set.
    if provider == "gemini":
        gem_env = os.getenv("LLM_TEMP_GEMINI")
        if gem_env is None:
            return None
        gem_str = gem_env.strip()
        if not gem_str or gem_str.lower() in ("false", "no", "off", "none"):
            return None
        if gem_str.lower() in ("true", "yes", "on", "1"):
            return role_temp
        try:
            return float(gem_str)
        except ValueError as e:
            raise RuntimeError("Invalid float value in LLM_TEMP_GEMINI") from e

    return role_temp


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
    """Sliding-window rate limiter tracking RPM, TPM, and optional daily request caps."""

    def __init__(
        self,
        provider: str,
        tpm: int,
        rpm: int,
        rpd: int | None = None,
    ):
        self.provider = provider
        self.tpm = tpm
        self.rpm = rpm
        self.rpd = rpd
        self.lock = threading.Lock()
        self.history: deque[tuple[float, int]] = deque()
        self.throttled_until: float = 0.0
        self.daily_date = datetime.now(UTC).date()
        self.daily_calls: int = 0
        self.daily_exhausted: bool = False

    def _prune(self, now: float) -> None:
        cutoff = now - 60.0
        while self.history and self.history[0][0] <= cutoff:
            self.history.popleft()

    def _prune_daily(self) -> None:
        today = datetime.now(UTC).date()
        if today != self.daily_date:
            self.daily_date = today
            self.daily_calls = 0
            self.daily_exhausted = False

    def rejection_reason(self, estimated_tokens: int) -> str | None:
        """Return clear skip reason if request exceeds limits, else None."""
        with self.lock:
            self._prune_daily()
            if self.daily_exhausted or (
                self.rpd is not None and self.daily_calls >= self.rpd
            ):
                cap = self.rpd if self.rpd is not None else "provider"
                return f"daily cap ({cap}) reached for {self.provider}"
            now = time.monotonic()
            if now < self.throttled_until:
                return f"provider '{self.provider}' throttled by Retry-After"
            if estimated_tokens > self.tpm:
                return (
                    f"prompt {estimated_tokens} tokens exceeds TPM limit {self.tpm}"
                )
            self._prune(now)
            current_tokens = sum(tokens for _, tokens in self.history)
            if current_tokens + estimated_tokens > self.tpm:
                return (
                    f"prompt {estimated_tokens} tokens exceeds remaining TPM capacity ({self.tpm - current_tokens})"
                )
            if len(self.history) + 1 > self.rpm:
                return f"RPM limit ({self.rpm}) reached for {self.provider}"
            return None

    def can_accept(self, estimated_tokens: int) -> bool:
        """Return False if prompt exceeds TPM, RPM, daily cap, or Retry-After."""
        return self.rejection_reason(estimated_tokens) is None

    def record_attempt(self) -> None:
        """Record an outgoing request attempt against the per-process daily count."""
        with self.lock:
            self._prune_daily()
            self.daily_calls += 1

    def record_usage(self, actual_tokens: int) -> None:
        with self.lock:
            now = time.monotonic()
            self._prune(now)
            self.history.append((now, actual_tokens))

    def throttle(self, seconds: float) -> None:
        with self.lock:
            self.throttled_until = max(
                self.throttled_until, time.monotonic() + seconds
            )

    def mark_daily_exhausted(self) -> None:
        with self.lock:
            self._prune_daily()
            self.daily_exhausted = True

    def reset(self) -> None:
        with self.lock:
            self.history.clear()
            self.throttled_until = 0.0
            self.daily_date = datetime.now(UTC).date()
            self.daily_calls = 0
            self.daily_exhausted = False


def _default_rpd_for(provider: str) -> int | None:
    rpd_env = os.getenv(f"{provider.upper()}_RPD")
    if rpd_env is not None and rpd_env.strip():
        return int(rpd_env)
    return PROVIDERS.get(provider, {}).get("default_rpd")


# Global provider rate limiters
_rate_limiters: dict[str, ProviderRateLimiter] = {
    p: ProviderRateLimiter(
        p,
        int(os.getenv(f"{p.upper()}_TPM", str(cfg["default_tpm"]))),
        int(os.getenv(f"{p.upper()}_RPM", str(cfg["default_rpm"]))),
        _default_rpd_for(p),
    )
    for p, cfg in PROVIDERS.items()
}


_rate_limiters_lock = threading.Lock()


def get_limiter(provider: str) -> ProviderRateLimiter:
    tpm_env = os.getenv(f"{provider.upper()}_TPM")
    rpm_env = os.getenv(f"{provider.upper()}_RPM")
    rpd_env = os.getenv(f"{provider.upper()}_RPD")
    default_rpd = PROVIDERS.get(provider, {}).get("default_rpd")

    with _rate_limiters_lock:
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
            rpd = _default_rpd_for(provider)
            limiter = ProviderRateLimiter(provider, tpm, rpm, rpd)
            _rate_limiters[provider] = limiter
            return limiter

    with limiter.lock:
        if tpm_env:
            limiter.tpm = int(tpm_env)
        if rpm_env:
            limiter.rpm = int(rpm_env)
        limiter.rpd = (
            int(rpd_env)
            if (rpd_env is not None and rpd_env.strip())
            else default_rpd
        )
    return limiter


def normalize_model_id(provider: str, model_id: str) -> str:
    """Normalize provider-specific model ID prefixes for consistent comparison."""
    mid = model_id.strip()
    if provider == "gemini":
        return mid.removeprefix("models/")
    return mid


def validate_openrouter_model(provider: str, model: str, env_var: str) -> None:
    """Reject non-':free' OpenRouter chain entries unless LLM_ALLOW_PAID=1."""
    if provider != "openrouter" or model.endswith(":free"):
        return
    if os.getenv("LLM_ALLOW_PAID", "").strip() == "1":
        return
    entry = f"{provider}:{model}"
    raise RuntimeError(
        f"Paid OpenRouter model '{entry}' in {env_var} is rejected; "
        "use a ':free' model or set LLM_ALLOW_PAID=1 to allow paid models"
    )


def parse_chain(
    role: str,
    require_keys: bool = True,
    enforce_paid_guard: bool = True,
) -> list[tuple[str, str]]:
    """Parse comma-separated provider:model string for role.

    Splits on the FIRST colon only so model IDs containing colons are preserved.
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
        if not prov or not model:
            raise RuntimeError(
                f"Invalid chain format in {env_var_name}: '{item}' (expected provider:model)"
            )
        if prov not in PROVIDERS:
            raise RuntimeError(f"Unknown LLM provider '{prov}' in {env_var_name}")
        if enforce_paid_guard:
            validate_openrouter_model(prov, model, env_var_name)
        if require_keys:
            key_var = PROVIDERS[prov]["env_key"]
            key_val = os.getenv(key_var, "").strip()
            if not key_val:
                raise RuntimeError(f"Missing required environment variable: {key_var}")
        entries.append((prov, model))

    if not entries:
        raise RuntimeError(f"No valid model chain configured in {env_var_name}")
    return entries


def get_used_providers() -> set[str]:
    """Return provider names referenced in any configured LLM_* role chain."""
    used: set[str] = set()
    for role in DEFAULT_CHAINS:
        for prov, _ in parse_chain(
            role, require_keys=False, enforce_paid_guard=False
        ):
            used.add(prov)
    return used


def get_primary_model_for_provider(provider: str) -> str:
    """Return the first configured chain model for provider, or a fallback model."""
    for role in DEFAULT_CHAINS:
        for prov, model in parse_chain(
            role, require_keys=False, enforce_paid_guard=False
        ):
            if prov == provider:
                return model
    fallback = {
        "gemini": "gemini-2.5-flash-lite",
        "mistral": "mistral-small-latest",
        "groq": "openai/gpt-oss-120b",
        "openrouter": "openai/gpt-oss-120b:free",
    }
    return fallback.get(provider, "default")


def _extract_status_code(error: Exception) -> int | None:
    status_code = getattr(error, "status_code", None)
    if status_code is None:
        response = getattr(error, "response", None)
        if response is not None:
            status_code = getattr(response, "status_code", None)
    return status_code


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


def _is_daily_limit_error(error: Exception) -> bool:
    """Detect provider daily quota / per-day rate limit errors."""
    msg = str(error).lower()
    return any(
        marker in msg
        for marker in (
            "daily limit",
            "daily quota",
            "requests per day",
            "per-day",
            "free-models-per-day",
        )
    )


def _is_rate_limit_or_transient(error: Exception) -> bool:
    """Identify HTTP 429, 5xx, daily-limit errors, or network timeouts."""
    status_code = _extract_status_code(error)
    if status_code in (429, 500, 502, 503, 504):
        return True
    if _is_daily_limit_error(error):
        return True
    msg = str(error).lower()
    return "rate limit" in msg or "429" in msg or "timeout" in msg or "timed out" in msg


def _is_temperature_rejection(error: Exception) -> bool:
    """Detect HTTP 400 rejecting temperature / unsupported parameter."""
    status_code = _extract_status_code(error)
    msg = str(error).lower()
    is_400 = status_code == 400 or "error code: 400" in msg or "http 400" in msg
    if not is_400:
        return False
    return (
        "temperature" in msg
        or "unsupported parameter" in msg
        or "unsupported_parameter" in msg
    )


def _invoke_with_method(
    client: ChatOpenAI,
    schema: type[BaseModel] | None,
    method: str | None,
    input_val: Any,
) -> tuple[Any, BaseMessage | None]:
    """Execute one invocation using either structured output method or plain invoke."""
    if schema is None:
        result = client.invoke(input_val)
        raw_msg = result if isinstance(result, BaseMessage) else None
        return result, raw_msg

    if method == "default":
        so_llm = client.with_structured_output(schema, include_raw=True)
    else:
        so_llm = client.with_structured_output(
            schema, method=method, include_raw=True
        )
    raw_out = so_llm.invoke(input_val)
    if isinstance(raw_out, dict) and raw_out.get("parsing_error"):
        raise RuntimeError(
            f"Structured output parsing failed with method {method}: {raw_out['parsing_error']}"
        )
    result = raw_out.get("parsed") if isinstance(raw_out, dict) else raw_out
    raw_msg = raw_out.get("raw") if isinstance(raw_out, dict) else None
    if result is None:
        raise RuntimeError(
            f"Structured output returned empty parsed payload with method {method}"
        )
    return result, raw_msg


def _extract_token_usage(
    raw_msg: BaseMessage | None, estimated_in: int, result: Any
) -> tuple[int, int]:
    if raw_msg is None:
        return estimated_in, 0
    usage_meta = getattr(raw_msg, "usage_metadata", None) or {}
    if isinstance(usage_meta, dict) and (
        "input_tokens" in usage_meta or "output_tokens" in usage_meta
    ):
        return (
            int(usage_meta.get("input_tokens", estimated_in)),
            int(usage_meta.get("output_tokens", estimate_tokens(result))),
        )
    metadata = getattr(raw_msg, "response_metadata", {}) or {}
    token_usage = metadata.get("token_usage", {}) or {}
    return (
        int(token_usage.get("prompt_tokens", estimated_in)),
        int(token_usage.get("completion_tokens", estimate_tokens(result))),
    )


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
            reason = limiter.rejection_reason(estimated_tokens)
            if reason is not None:
                logger.warning(
                    "Rate/size guard skipped %s:%s (%s); preferring next chain entry",
                    provider,
                    model,
                    reason,
                )
                errors.append(f"{provider}:{model} skipped ({reason})")
                continue

            prov_cfg = PROVIDERS[provider]
            api_key = os.getenv(prov_cfg["env_key"], "")
            client_kwargs: dict[str, Any] = {
                "model": model,
                "api_key": api_key,
                "base_url": prov_cfg["base_url"],
                "timeout": 60,
                "max_retries": 0,
            }
            temp = resolve_temperature(
                self.role, provider, model, self.temperature
            )
            if temp is not None:
                client_kwargs["temperature"] = temp

            client = ChatOpenAI(**client_kwargs)
            methods = (
                ["default", "function_calling", "json_mode"]
                if self.schema is not None
                else [None]
            )
            success = False
            result = None
            raw_msg = None
            last_err: Exception | None = None

            start_time = time.monotonic()
            for method in methods:
                retry_reason = limiter.rejection_reason(estimated_tokens)
                if retry_reason is not None:
                    last_err = RuntimeError(
                        f"Rate/cap guard halted retry: {retry_reason}"
                    )
                    break
                retried_without_temp = False
                while True:
                    try:
                        limiter.record_attempt()
                        result, raw_msg = _invoke_with_method(
                            client, self.schema, method, input_val
                        )
                        success = True
                        break
                    except Exception as ex:
                        if (
                            not retried_without_temp
                            and "temperature" in client_kwargs
                            and _is_temperature_rejection(ex)
                        ):
                            logger.info(
                                "Model '%s' rejected temperature (HTTP 400); retrying once without temperature",
                                model,
                            )
                            _remember_temperature_rejected(model)
                            client_kwargs.pop("temperature", None)
                            client = ChatOpenAI(**client_kwargs)
                            retried_without_temp = True
                            continue
                        last_err = ex
                        break

                if success:
                    break
                assert last_err is not None
                if _is_daily_limit_error(last_err):
                    limiter.mark_daily_exhausted()
                    break
                if _is_rate_limit_or_transient(last_err):
                    break
                logger.info(
                    "Structured output method '%s' failed for %s:%s: %s; trying next method",
                    method,
                    provider,
                    model,
                    last_err,
                )

            latency = time.monotonic() - start_time

            if success:
                in_tokens, out_tokens = _extract_token_usage(
                    raw_msg, estimated_tokens, result
                )
                limiter.record_usage(in_tokens + out_tokens)

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

            retry_after = (
                _parse_retry_after(last_err) if last_err is not None else None
            )
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
    """List normalized model IDs returned by each configured provider /models endpoint."""
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
            ids = sorted(
                {normalize_model_id(prov, str(m.id)) for m in models_resp.data}
            )
            results[prov] = ids
        except Exception as e:
            logger.warning("Failed to fetch models from provider '%s': %s", prov, e)
            results[prov] = []
    return results


def check_chain_models(
    available_models: dict[str, list[str]] | None = None,
) -> list[dict[str, Any]]:
    """Verify configured chain entries against normalized model lists and flag mismatches."""
    if available_models is None:
        available_models = list_provider_models()
    checks: list[dict[str, Any]] = []

    for role in ("triage", "analyst", "fixer", "spokesperson"):
        try:
            chain = parse_chain(role, require_keys=False)
        except Exception as e:
            checks.append(
                {"role": role, "entry": "none", "status": f"FLAG: {e}"}
            )
            continue

        for prov, model in chain:
            models_for_prov = available_models.get(prov, [])
            normalized_available = {
                normalize_model_id(prov, m) for m in models_for_prov
            }
            normalized_model = normalize_model_id(prov, model)
            key_var = PROVIDERS[prov]["env_key"]
            key = os.getenv(key_var, "").strip()
            if not key:
                status = f"FLAG: missing {key_var}"
            elif not normalized_available:
                status = "unverified (could not list models)"
            elif normalized_model in normalized_available:
                status = "ok"
            else:
                status = f"FLAG: '{model}' not in provider model list"

            checks.append(
                {"role": role, "entry": f"{prov}:{model}", "status": status}
            )
    return checks
