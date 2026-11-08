"""Unit tests for SavageOps Phase 2 LLM Layer:

Multi-provider chains, failover order, rate limiting, prompt-size guard,
structured-output fallbacks, metrics, telemetry, and anti-leak client checks.
"""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from langchain_core.messages import AIMessage
from pydantic import BaseModel

import savageops.llm as llm_module
from savageops.llm import (
    PROVIDERS,
    can_pass_temperature,
    get_limiter,
    get_llm,
    invoke_guarded,
    parse_chain,
)


class SampleOutput(BaseModel):
    summary: str
    confidence: float


@pytest.fixture(autouse=True)
def setup_dummy_keys(monkeypatch):
    """Ensure dummy keys exist for all supported providers during tests."""
    monkeypatch.setenv("GEMINI_API_KEY", "dummy-gemini")
    monkeypatch.setenv("MISTRAL_API_KEY", "dummy-mistral")
    monkeypatch.setenv("GROQ_API_KEY", "dummy-groq")
    for prov in PROVIDERS:
        get_limiter(prov).reset()


# 1. Chain Parsing Tests
def test_parse_chain_defaults():
    triage_chain = parse_chain("triage")
    assert triage_chain == [
        ("gemini", "gemini-3.5-flash-lite"),
        ("mistral", "mistral-small-latest"),
    ]

    analyst_chain = parse_chain("analyst")
    assert analyst_chain == [
        ("gemini", "gemini-3.8-flash"),
        ("mistral", "mistral-large-latest"),
    ]

    fixer_chain = parse_chain("fixer")
    assert fixer_chain == [
        ("mistral", "devstral-2-2512"),
        ("gemini", "gemini-3.8-flash"),
    ]

    spokesperson_chain = parse_chain("spokesperson")
    assert spokesperson_chain == [
        ("groq", "openai/gpt-oss-120b"),
        ("gemini", "gemini-3.5-flash-lite"),
    ]


def test_parse_chain_custom_override(monkeypatch):
    monkeypatch.setenv("LLM_TRIAGE", "mistral:custom-model,gemini:alt-model")
    chain = parse_chain("triage")
    assert chain == [("mistral", "custom-model"), ("gemini", "alt-model")]


def test_parse_chain_unknown_provider_raises_clear_error(monkeypatch):
    monkeypatch.setenv("LLM_TRIAGE", "unsupported_prov:model-x")
    with pytest.raises(RuntimeError) as exc_info:
        parse_chain("triage")
    assert "Unknown LLM provider 'unsupported_prov' in LLM_TRIAGE" in str(
        exc_info.value
    )


def test_parse_chain_missing_key_names_only_the_variable(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "")
    with pytest.raises(RuntimeError) as exc_info:
        parse_chain("triage")
    assert str(exc_info.value) == "Missing required environment variable: GEMINI_API_KEY"


# 2. Temperature Handling Tests
def test_temperature_rejection_rules():
    assert can_pass_temperature("gpt-4o") is True
    assert can_pass_temperature("gemini-3.5-flash") is True
    assert can_pass_temperature("mistral-large") is True
    assert can_pass_temperature("o1-mini") is False
    assert can_pass_temperature("o3-mini") is False
    assert can_pass_temperature("gpt-5-preview") is False
    assert can_pass_temperature("devstral-2-2512") is False


# 3. Prompt-Size Guard & Rate Limiter Tests
def test_prompt_size_guard_skips_when_exceeding_tpm(monkeypatch):
    # Groq has default 8000 TPM
    monkeypatch.setenv("LLM_TRIAGE", "groq:groq-model,gemini:gemini-fallback")
    monkeypatch.setenv("GROQ_TPM", "100")

    runner = get_llm("triage")
    huge_prompt = "x" * 2000  # ~550 estimated tokens, exceeds 100 TPM

    mock_gemini_client = MagicMock()
    mock_gemini_client.invoke.return_value = AIMessage(
        content="fallback response",
        response_metadata={"token_usage": {"prompt_tokens": 550, "completion_tokens": 10}},
    )

    with patch("savageops.llm.ChatOpenAI") as mock_chat_openai:
        mock_chat_openai.return_value = mock_gemini_client
        res = runner.invoke(huge_prompt)

    # Asserts Groq was skipped and fallback was called
    assert res.content == "fallback response"
    call_kwargs = mock_chat_openai.call_args[1]
    assert call_kwargs["model"] == "gemini-fallback"


# 4. Failover Order (429, 5xx, Timeout)
def test_failover_on_429_http_error(monkeypatch):
    monkeypatch.setenv("LLM_TRIAGE", "gemini:primary-model,mistral:backup-model")

    mock_primary = MagicMock()
    # Simulate HTTP 429
    err_429 = RuntimeError("HTTP 429 Too Many Requests")
    setattr(err_429, "status_code", 429)
    mock_primary.invoke.side_effect = err_429

    mock_backup = MagicMock()
    mock_backup.invoke.return_value = AIMessage(
        content="backup succeeded",
        response_metadata={"token_usage": {"prompt_tokens": 50, "completion_tokens": 10}},
    )

    with patch("savageops.llm.ChatOpenAI") as mock_chat_openai:
        mock_chat_openai.side_effect = [mock_primary, mock_backup]
        runner = get_llm("triage")
        res = runner.invoke("test prompt")

    assert res.content == "backup succeeded"
    assert mock_chat_openai.call_count == 2


def test_retry_after_honored_in_rate_limiter(monkeypatch):
    monkeypatch.setenv("LLM_TRIAGE", "gemini:m1,mistral:m2")
    limiter = get_limiter("gemini")

    mock_client1 = MagicMock()
    err_429 = RuntimeError("Rate limited")
    setattr(err_429, "status_code", 429)
    resp = MagicMock()
    resp.headers = {"retry-after": "10"}
    setattr(err_429, "response", resp)
    mock_client1.invoke.side_effect = err_429

    mock_client2 = MagicMock()
    mock_client2.invoke.return_value = AIMessage(
        content="fallback ok",
        response_metadata={"token_usage": {"prompt_tokens": 10, "completion_tokens": 5}},
    )

    with patch("savageops.llm.ChatOpenAI", side_effect=[mock_client1, mock_client2]):
        runner = get_llm("triage")
        res = runner.invoke("prompt")

    assert res.content == "fallback ok"
    # Verify limiter recorded throttled state
    assert limiter.can_accept(10) is False


def test_all_providers_fail_raises_clear_error(monkeypatch):
    monkeypatch.setenv("LLM_TRIAGE", "gemini:m1,mistral:m2")

    mock_client1 = MagicMock()
    mock_client1.invoke.side_effect = RuntimeError("Primary failure")
    mock_client2 = MagicMock()
    mock_client2.invoke.side_effect = RuntimeError("Secondary failure")

    with patch("savageops.llm.ChatOpenAI", side_effect=[mock_client1, mock_client2]):
        runner = get_llm("triage")
        with pytest.raises(RuntimeError) as exc_info:
            runner.invoke("test")

    assert "All LLM providers in chain failed for role 'triage'" in str(exc_info.value)
    assert "Primary failure" in str(exc_info.value)
    assert "Secondary failure" in str(exc_info.value)


# 5. Structured Output Fallback
def test_structured_output_method_fallbacks(monkeypatch):
    monkeypatch.setenv("LLM_TRIAGE", "gemini:m1")

    mock_client = MagicMock()
    # 1. Default method fails
    mock_so_default = MagicMock()
    mock_so_default.invoke.side_effect = RuntimeError("Schema validation error")

    # 2. function_calling method succeeds
    mock_so_fc = MagicMock()
    mock_so_fc.invoke.return_value = {
        "parsed": SampleOutput(summary="parsed via function calling", confidence=0.9),
        "raw": AIMessage(content="{}", response_metadata={"token_usage": {"prompt_tokens": 20, "completion_tokens": 15}}),
        "parsing_error": None,
    }

    def fake_with_structured(schema, method=None, include_raw=False):
        if method is None:
            return mock_so_default
        if method == "function_calling":
            return mock_so_fc
        return MagicMock()

    mock_client.with_structured_output.side_effect = fake_with_structured

    with patch("savageops.llm.ChatOpenAI", return_value=mock_client):
        runner = get_llm("triage", schema=SampleOutput)
        res = runner.invoke("analyze this")

    assert isinstance(res, SampleOutput)
    assert res.summary == "parsed via function calling"
    assert res.confidence == 0.9


# 6. Telemetry & agent_runs persistence
def test_telemetry_recorded_to_agent_runs_and_metrics(monkeypatch):
    monkeypatch.setenv("LLM_TRIAGE", "gemini:m1")

    mock_client = MagicMock()
    mock_client.invoke.return_value = AIMessage(
        content="success result",
        response_metadata={"token_usage": {"prompt_tokens": 42, "completion_tokens": 18}},
    )

    from savageops.db import _memory_agent_runs

    _memory_agent_runs.clear()

    with patch("savageops.llm.ChatOpenAI", return_value=mock_client):
        runner = get_llm("triage")
        res = invoke_guarded(runner, {"input": "test prompt"}, incident_id="inc-phase2-test")

    assert res.content == "success result"
    # Assert agent_runs logged the LLM call with metadata
    llm_runs = [r for r in _memory_agent_runs if r.get("kind") == "llm_call"]
    assert len(llm_runs) == 1
    run = llm_runs[0]
    assert run["incident_id"] == "inc-phase2-test"
    assert run["provider"] == "gemini"
    assert run["model"] == "m1"
    assert run["role"] == "triage"
    assert run["input_tokens"] == 42
    assert run["output_tokens"] == 18
    assert run["fallback_depth"] == 0
    assert run["latency"] >= 0.0


# 7. Grep Test: Verify no agent builds a client outside get_llm()
def test_grep_no_direct_llm_clients_in_agents():
    agents_dir = Path("savageops/agents")
    py_files = list(agents_dir.glob("*.py"))
    assert len(py_files) >= 4, "Expected triage, analyst, fixer, spokesperson"

    forbidden_patterns = [
        "ChatOpenAI(",
        "OpenAI(",
        "create_chat_model(",
        "ChatAnthropic(",
    ]

    violations = []
    for f in py_files:
        content = f.read_text(encoding="utf-8")
        for pat in forbidden_patterns:
            if pat in content:
                violations.append(f"{f.name} contains forbidden pattern '{pat}'")

    assert not violations, f"Agents must not build LLM clients outside get_llm(): {violations}"


# 8. CLI models & doctor tests
def test_cli_models_check():
    mock_gemini_models = MagicMock()
    mock_gemini_models.data = [MagicMock(id="gemini-3.5-flash-lite"), MagicMock(id="gemini-3.8-flash")]
    mock_mistral_models = MagicMock()
    mock_mistral_models.data = [MagicMock(id="mistral-small-latest"), MagicMock(id="mistral-large-latest")]
    mock_groq_models = MagicMock()
    mock_groq_models.data = [MagicMock(id="openai/gpt-oss-120b")]

    def fake_openai(api_key, base_url, timeout):
        client = MagicMock()
        if "google" in base_url:
            client.models.list.return_value = mock_gemini_models
        elif "mistral" in base_url:
            client.models.list.return_value = mock_mistral_models
        else:
            client.models.list.return_value = mock_groq_models
        return client

    with patch("openai.OpenAI", side_effect=fake_openai):
        checks = llm_module.check_chain_models()
        assert len(checks) >= 4
        assert all("FLAG" not in c["status"] for c in checks if c["entry"] != "mistral:devstral-2-2512")
