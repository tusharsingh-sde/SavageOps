"""Unit tests for SavageOps Phase 2 & 2b LLM Layer:

Multi-provider chains, colon-safe parsing, OpenRouter 50/day cap & failover,
unused provider skipping, temperature defaults/Gemini omission/400 retry,
rate limiting, prompt-size guard, structured-output fallbacks, metrics,
telemetry, CLI models/doctor, and anti-leak client checks.
"""

import sys
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
    reset_temperature_rejections,
    resolve_temperature,
)


class SampleOutput(BaseModel):
    summary: str
    confidence: float


@pytest.fixture(autouse=True)
def setup_dummy_keys(monkeypatch):
    """Ensure dummy keys exist for active providers during tests and reset limiters."""
    monkeypatch.setenv("GEMINI_API_KEY", "dummy-gemini")
    monkeypatch.setenv("MISTRAL_API_KEY", "dummy-mistral")
    monkeypatch.setenv("GROQ_API_KEY", "dummy-groq")
    monkeypatch.setenv("OPENROUTER_API_KEY", "dummy-openrouter")
    for var in (
        "LLM_TRIAGE",
        "LLM_ANALYST",
        "LLM_FIXER",
        "LLM_SPOKESPERSON",
        "LLM_TEMPERATURE_TRIAGE",
        "LLM_TEMPERATURE_ANALYST",
        "LLM_TEMPERATURE_FIXER",
        "LLM_TEMPERATURE_SPOKESPERSON",
        "LLM_TEMP_GEMINI",
    ):
        monkeypatch.delenv(var, raising=False)
    for prov in PROVIDERS:
        get_limiter(prov).reset()
    reset_temperature_rejections()


# 1. Chain Parsing Tests (including 0, 1, and 2 colons in model ID)
def test_parse_chain_defaults():
    triage_chain = parse_chain("triage")
    assert triage_chain == [
        ("gemini", "gemini-3.5-flash-lite"),
        ("gemini", "gemini-3.6-flash"),
    ]

    analyst_chain = parse_chain("analyst")
    assert analyst_chain == [
        ("gemini", "gemini-3.8-flash"),
        ("openrouter", "openai/gpt-oss-120b:free"),
    ]

    fixer_chain = parse_chain("fixer")
    assert fixer_chain == [
        ("openrouter", "qwen/qwen3-coder:free"),
        ("gemini", "gemini-3.8-flash"),
    ]

    spokesperson_chain = parse_chain("spokesperson")
    assert spokesperson_chain == [
        ("groq", "openai/gpt-oss-120b"),
        ("gemini", "gemini-3.5-flash-lite"),
    ]


def test_parse_chain_colons_in_model_id_0_1_2(monkeypatch):
    # 0 colons in model ID, 1 colon in model ID, 2 colons in model ID
    monkeypatch.setenv(
        "LLM_TRIAGE",
        "gemini:gemini-3.5-flash-lite,openrouter:openai/gpt-oss-120b:free,openrouter:qwen/qwen3:32b:free",
    )
    chain = parse_chain("triage")
    assert chain == [
        ("gemini", "gemini-3.5-flash-lite"),
        ("openrouter", "openai/gpt-oss-120b:free"),
        ("openrouter", "qwen/qwen3:32b:free"),
    ]
    assert chain[0][1].count(":") == 0
    assert chain[1][1].count(":") == 1
    assert chain[2][1].count(":") == 2


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
    assert can_pass_temperature("devstral-2-2512") is True
    assert can_pass_temperature("o1-mini") is False
    assert can_pass_temperature("o3-mini") is False
    assert can_pass_temperature("gpt-5-preview") is False


def test_temperature_role_defaults_and_gemini_omission(monkeypatch):
    monkeypatch.delenv("LLM_TEMP_GEMINI", raising=False)
    monkeypatch.delenv("LLM_TEMPERATURE_FIXER", raising=False)
    monkeypatch.delenv("LLM_TEMPERATURE_TRIAGE", raising=False)
    monkeypatch.delenv("LLM_TEMPERATURE_ANALYST", raising=False)
    monkeypatch.delenv("LLM_TEMPERATURE_SPOKESPERSON", raising=False)

    # Non-gemini providers receive configured role default
    assert resolve_temperature("fixer", "openrouter", "qwen/qwen3-coder:free") == 0.1
    assert resolve_temperature("triage", "mistral", "mistral-small-latest") == 0.1
    assert resolve_temperature("analyst", "openrouter", "openai/gpt-oss-120b:free") == 0.2
    assert resolve_temperature("spokesperson", "groq", "openai/gpt-oss-120b") == 0.7

    # Override via LLM_TEMPERATURE_<ROLE>
    monkeypatch.setenv("LLM_TEMPERATURE_FIXER", "0.05")
    assert resolve_temperature("fixer", "openrouter", "qwen/qwen3-coder:free") == 0.05

    # Gemini defaults to omitting temperature (provider default) unless LLM_TEMP_GEMINI is set
    assert resolve_temperature("triage", "gemini", "gemini-3.5-flash-lite") is None

    monkeypatch.setenv("LLM_TEMP_GEMINI", "true")
    assert resolve_temperature("analyst", "gemini", "gemini-3.8-flash") == 0.2

    monkeypatch.setenv("LLM_TEMP_GEMINI", "0.35")
    assert resolve_temperature("analyst", "gemini", "gemini-3.8-flash") == 0.35


def test_temperature_http_400_retry_and_process_cache(monkeypatch):
    monkeypatch.setenv("LLM_FIXER", "mistral:devstral-2-2512")

    calls_kwargs: list[dict] = []

    mock_client_fail = MagicMock()
    err_400 = RuntimeError("Error code: 400 - Unsupported parameter: temperature")
    setattr(err_400, "status_code", 400)
    mock_client_fail.invoke.side_effect = err_400

    mock_client_ok = MagicMock()
    mock_client_ok.invoke.return_value = AIMessage(
        content="ok without temp",
        response_metadata={"token_usage": {"prompt_tokens": 15, "completion_tokens": 5}},
    )

    def fake_chat_openai(**kwargs):
        calls_kwargs.append(dict(kwargs))
        if "temperature" in kwargs:
            return mock_client_fail
        return mock_client_ok

    with patch("savageops.llm.ChatOpenAI", side_effect=fake_chat_openai):
        runner = get_llm("fixer")
        res1 = runner.invoke("fix bug 1")
        assert res1.content == "ok without temp"

        # 1st attempt sent temperature=0.1, rejected with 400, retried once without it
        assert len(calls_kwargs) == 2
        assert calls_kwargs[0]["temperature"] == 0.1
        assert "temperature" not in calls_kwargs[1]

        # 2nd invocation in same process remembers rejection and omits temperature immediately
        res2 = runner.invoke("fix bug 2")
        assert res2.content == "ok without temp"
        assert len(calls_kwargs) == 3
        assert "temperature" not in calls_kwargs[2]


# 3. Prompt-Size Guard & OpenRouter 50/day Cap & Rate Limiter Tests
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


def test_openrouter_provider_config_and_50_day_cap_failover(monkeypatch):
    assert PROVIDERS["openrouter"]["base_url"] == "https://openrouter.ai/api/v1"
    assert PROVIDERS["openrouter"]["env_key"] == "OPENROUTER_API_KEY"
    assert PROVIDERS["openrouter"]["default_rpm"] == 20
    assert PROVIDERS["openrouter"]["default_rpd"] == 50

    monkeypatch.setenv("LLM_FIXER", "openrouter:qwen/qwen3-coder:free,gemini:gemini-3.8-flash")
    # Override RPM high enough so all 50 calls hit daily cap without triggering 20/min RPM
    monkeypatch.setenv("OPENROUTER_RPM", "100")
    get_limiter("openrouter").reset()

    called_models: list[str] = []

    def fake_chat_openai(**kwargs):
        called_models.append(kwargs["model"])
        client = MagicMock()
        client.invoke.return_value = AIMessage(
            content=f"ok from {kwargs['model']}",
            response_metadata={"token_usage": {"prompt_tokens": 10, "completion_tokens": 5}},
        )
        return client

    with patch("savageops.llm.ChatOpenAI", side_effect=fake_chat_openai):
        runner = get_llm("fixer")
        for _ in range(50):
            res = runner.invoke("small prompt")
            assert res.content == "ok from qwen/qwen3-coder:free"

        # 51st call hits per-process 50/day cap and moves to next chain entry (gemini)
        res_51 = runner.invoke("51st prompt")
        assert res_51.content == "ok from gemini-3.8-flash"

    assert called_models[:50] == ["qwen/qwen3-coder:free"] * 50
    assert called_models[50] == "gemini-3.8-flash"


def test_openrouter_daily_limit_error_fails_over(monkeypatch):
    monkeypatch.setenv("LLM_ANALYST", "openrouter:openai/gpt-oss-120b:free,gemini:gemini-3.8-flash")

    mock_or = MagicMock()
    err_daily = RuntimeError("HTTP 429 free-models-per-day daily limit reached")
    setattr(err_daily, "status_code", 429)
    mock_or.invoke.side_effect = err_daily

    mock_gem = MagicMock()
    mock_gem.invoke.return_value = AIMessage(
        content="gemini takeover",
        response_metadata={"token_usage": {"prompt_tokens": 10, "completion_tokens": 5}},
    )

    with patch("savageops.llm.ChatOpenAI", side_effect=[mock_or, mock_gem]) as mock_cls:
        runner = get_llm("analyst")
        res = runner.invoke("prompt 1")
        assert res.content == "gemini takeover"
        assert mock_cls.call_count == 2

    # Limiter remembers openrouter daily exhaustion and skips it directly on next call
    with patch("savageops.llm.ChatOpenAI", return_value=mock_gem) as mock_cls2:
        res2 = runner.invoke("prompt 2")
        assert res2.content == "gemini takeover"
        assert mock_cls2.call_args[1]["model"] == "gemini-3.8-flash"


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


# 8. Unused provider skipped & CLI models / doctor tests
def test_unused_provider_missing_key_skipped_in_startup_doctor_and_models(monkeypatch, capsys):
    import savageops.cli as cli_mod
    import savageops.config as cfg_mod
    import savageops.health as health_mod

    # Ensure mistral is NOT in any default chain and has no key
    monkeypatch.delenv("MISTRAL_API_KEY", raising=False)
    for role in ("LLM_TRIAGE", "LLM_ANALYST", "LLM_FIXER", "LLM_SPOKESPERSON"):
        monkeypatch.delenv(role, raising=False)

    # 1) Production startup config succeeds without MISTRAL_API_KEY
    monkeypatch.setattr(cfg_mod, "APP_ENV", "production")
    monkeypatch.setattr(cfg_mod, "GITHUB_TOKEN", "dummy")
    monkeypatch.setattr(cfg_mod, "TARGET_REPO", "owner/repo")
    monkeypatch.setattr(cfg_mod, "SUPABASE_URL", "https://dummy.supabase.co")
    monkeypatch.setattr(cfg_mod, "SUPABASE_SERVICE_KEY", "dummy")
    monkeypatch.setattr(cfg_mod, "SUPABASE_JWT_SECRET", "dummy")
    monkeypatch.setattr(cfg_mod, "WEBHOOK_SECRET", "dummy")
    monkeypatch.setattr(cfg_mod, "METRICS_TOKEN", "dummy")
    monkeypatch.setattr(cfg_mod, "DATABASE_URL", "postgresql://dummy")
    monkeypatch.setattr(cfg_mod, "ADMIN_USER_IDS", ["00000000-0000-0000-0000-000000000000"])
    cfg_mod.validate_startup_config()  # Must NOT raise on missing MISTRAL_API_KEY

    # If mistral IS added to a chain without MISTRAL_API_KEY, startup MUST fail naming MISTRAL_API_KEY
    monkeypatch.setenv("LLM_TRIAGE", "mistral:mistral-small-latest")
    with pytest.raises(RuntimeError, match="MISTRAL_API_KEY"):
        cfg_mod.validate_startup_config()

    # Reset LLM_TRIAGE back to default (mistral unused) for doctor & models check
    monkeypatch.delenv("LLM_TRIAGE", raising=False)

    # 2) doctor pings each configured provider ONCE (gemini, groq, openrouter) and marks mistral skipped
    pinged_urls: list[str] = []
    ping_max_tokens: list[int] = []

    def fake_openai_doctor(api_key, base_url, timeout):
        client = MagicMock()

        def fake_create(**kwargs):
            pinged_urls.append(base_url)
            ping_max_tokens.append(kwargs.get("max_tokens"))
            return MagicMock()

        client.chat.completions.create.side_effect = fake_create
        return client

    with (
        patch("openai.OpenAI", side_effect=fake_openai_doctor),
        patch("savageops.health.db") as mock_db,
        patch("savageops.health._get_client_and_repo", return_value=(MagicMock(), MagicMock(default_branch="main"))),
        patch("savageops.health.httpx.get", return_value=MagicMock(raise_for_status=MagicMock())),
        patch("savageops.health.LOKI_URL", ""),
        patch("savageops.health.GRAFANA_TOKEN", ""),
        patch("savageops.health.GRAFANA_URL", "https://grafana.example.com"),
    ):
        mock_db.table.return_value.select.return_value.limit.return_value.execute.return_value = MagicMock()
        health_res = health_mod.run_checks(ping_llms=True)

    assert health_res["llm_mistral"] == "skipped"
    assert health_res["llm_gemini"] == "ok"
    assert health_res["llm_groq"] == "ok"
    assert health_res["llm_openrouter"] == "ok"
    # Exactly ONE ping per configured provider (3 providers, NOT per chain entry), each with max_tokens=5
    assert len(pinged_urls) == 3
    assert ping_max_tokens == [5, 5, 5]

    # Run doctor CLI and verify exit code 0 + skipped shown
    with (
        patch("savageops.health.run_checks", return_value=health_res),
        patch.object(sys, "argv", ["savageops", "doctor"]),
    ):
        with pytest.raises(SystemExit) as exit_info:
            cli_mod.main()
        assert exit_info.value.code == 0
    doc_out = capsys.readouterr().out
    assert "llm_mistral" in doc_out and "skipped" in doc_out

    # 3) models CLI lists /models IDs (never completions) and shows skipped for mistral
    with (
        patch.object(
            llm_module,
            "list_provider_models",
            return_value={
                "gemini": ["gemini-3.5-flash-lite", "gemini-3.6-flash", "gemini-3.8-flash"],
                "mistral": [],
                "groq": ["openai/gpt-oss-120b"],
                "openrouter": ["openai/gpt-oss-120b:free", "qwen/qwen3-coder:free"],
            },
        ),
        patch.object(sys, "argv", ["savageops", "models"]),
    ):
        with pytest.raises(SystemExit) as exit_info:
            cli_mod.main()
        assert exit_info.value.code == 0
    models_out = capsys.readouterr().out
    assert "[MISTRAL] skipped" in models_out
    assert "qwen/qwen3-coder:free" in models_out


def test_cli_models_check():
    mock_gemini_models = MagicMock()
    mock_gemini_models.data = [
        MagicMock(id="gemini-3.5-flash-lite"),
        MagicMock(id="gemini-3.6-flash"),
        MagicMock(id="gemini-3.8-flash"),
    ]
    mock_openrouter_models = MagicMock()
    mock_openrouter_models.data = [
        MagicMock(id="openai/gpt-oss-120b:free"),
        MagicMock(id="qwen/qwen3-coder:free"),
    ]
    mock_groq_models = MagicMock()
    mock_groq_models.data = [MagicMock(id="openai/gpt-oss-120b")]

    def fake_openai(api_key, base_url, timeout):
        client = MagicMock()
        if "google" in base_url:
            client.models.list.return_value = mock_gemini_models
        elif "openrouter" in base_url:
            client.models.list.return_value = mock_openrouter_models
        elif "groq" in base_url:
            client.models.list.return_value = mock_groq_models
        else:
            client.models.list.return_value = MagicMock(data=[])
        return client

    with patch("openai.OpenAI", side_effect=fake_openai):
        checks = llm_module.check_chain_models()
        assert len(checks) == 8
        assert all("FLAG" not in c["status"] for c in checks)
