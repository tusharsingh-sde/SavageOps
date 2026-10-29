"""Central guard for daily LLM request budgets."""

import threading
from datetime import UTC, datetime

from langchain_openai import ChatOpenAI

from savageops.config import MAX_DAILY_LLM_CALLS, MAX_DAILY_LLM_TOKENS

_lock = threading.Lock()
_usage_date = datetime.now(UTC).date()
_calls_today = 0
_tokens_reserved_today = 0


def invoke_guarded(runnable, inputs: dict, incident_id: str):
    """Enforce a process-wide daily request ceiling before invoking a model."""
    global _usage_date, _calls_today, _tokens_reserved_today
    today = datetime.now(UTC).date()
    estimated_tokens = max(1, len(str(inputs))) + 2048
    with _lock:
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
    return runnable.invoke(inputs)


def create_chat_model(model: str, temperature: float | None = 0):
    """Create a bounded OpenAI client, omitting temperature for reasoning models."""
    kwargs = {"model": model, "timeout": 60, "max_retries": 3}
    kwargs["max_completion_tokens" if model.lower().startswith(("o1", "o3", "o4", "gpt-5")) else "max_tokens"] = 2048
    if temperature is not None and not model.lower().startswith(("o1", "o3", "o4", "gpt-5")):
        kwargs["temperature"] = temperature
    return ChatOpenAI(**kwargs)
