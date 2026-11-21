# Phase 2 Report: LLM Layer

## 1. Goal and Exit Criteria

**Goal**: Implement the multi-provider LLM layer (`savageops/llm.py`) providing `get_llm(role)` for roles `triage`, `analyst`, `fixer`, and `spokesperson` using ordered chains of OpenAI-compatible providers (`gemini`, `mistral`, `groq`). Add automatic failover across HTTP 429/5xx/timeout and structured-output fallback (`default`, `function_calling`, `json_mode`), honor `Retry-After`, enforce per-provider rate limits (including Groq 8000 TPM limit) and prompt-size guards, stream LLM call telemetry to Prometheus and `agent_runs`, implement `savageops models` and `savageops doctor` commands, add `model_usage_stats` SQL view migration, remove all OpenAI legacy configurations, and verify via mock-based offline unit tests.

**Exit Criteria**:
- `savageops/llm.py`: Implements `get_llm(role)` reading `LLM_<ROLE>` ordered chains.
- Failover on 429, 5xx, timeout, and structured output parsing failures across methods.
- Per-provider rate limiter (RPM/TPM, Groq 8000 TPM) and prompt-size guard preferring next chain entry over exceeding TPM.
- Telemetry: every LLM call writes provider, model, role, input/output tokens, latency, and fallback_depth to Prometheus metrics and `agent_runs`.
- CLI commands: `savageops models` lists returned model IDs and flags missing chain entries; `savageops doctor` pings configured providers with `max_tokens=5`.
- Removal: All legacy OpenAI settings removed, `.env.example` and `templates/env.example` updated with `LLM_*` variables; grep test asserts no direct client creation in agents outside `get_llm()`.
- SQL View: `deploy/supabase_migration_phase2.sql` adds columns to `agent_runs` and creates `model_usage_stats` view.
- Ruff and pytest clean in neutralized session.
- Live provider checks marked `BLOCKED (user-run)` with exact commands for user execution.

---

## 2. Changed Files

| Area / File | Before | After | Why |
|---|---|---|---|
| `savageops/llm.py` | Minimal daily request counter and ChatOpenAI factory | Multi-provider chain management (`PROVIDERS`, `DEFAULT_CHAINS`, `RoleChainRunner`), rate limiter, prompt-size guard, structured output fallbacks, and telemetry recording | Core Phase 2 implementation. |
| `savageops/config.py` | Legacy `OPENAI_API_KEY` and `OPENAI_MODEL` | `GEMINI_API_KEY`, `MISTRAL_API_KEY`, `GROQ_API_KEY`, and `LLM_<ROLE>` configurations; provider key validation on startup | Remove legacy OpenAI settings and configure multi-provider chains. |
| `savageops/metrics.py` | Basic agent and ingest metrics | Added `LLM_CALLS`, `LLM_TOKENS`, `LLM_LATENCY`, `LLM_FALLBACK_DEPTH` Prometheus metrics | LLM call telemetry. |
| `savageops/db.py` | `log_run` logged only basic input/output/error | Added `provider`, `model`, `role`, `input_tokens`, `output_tokens`, `latency`, `fallback_depth` | Record LLM call details in `agent_runs`. |
| `savageops/health.py` | Checked single OpenAI models endpoint | Provider pings for configured LLM providers with `max_tokens=5` | Multi-provider doctor health checks. |
| `savageops/cli.py` | Commands: `run`, `doctor`, `simulate`, `init` | Added `models` subparser; updated `doctor` diagnostics | CLI verification of provider models and chains. |
| `savageops/agents/triage.py` | Called `create_chat_model(OPENAI_MODEL)` | Calls `get_llm("triage", schema=TriageReport)` | Multi-provider chain integration. |
| `savageops/agents/analyst.py` | Called `create_chat_model(OPENAI_MODEL)` | Calls `get_llm("analyst", schema=AnalystResult)` | Multi-provider chain integration. |
| `savageops/agents/fixer.py` | Called `create_chat_model(OPENAI_MODEL)` | Calls `get_llm("fixer", schema=FixedFile)` | Multi-provider chain integration. |
| `savageops/agents/spokesperson.py` | Called `create_chat_model(OPENAI_MODEL, temperature=0.7)` | Calls `get_llm("spokesperson", schema=SavageSummary, temperature=0.7)` | Multi-provider chain integration. |
| `.env.example` | Included `OPENAI_API_KEY` and `OPENAI_MODEL` | Replaced with `GEMINI_API_KEY`, `MISTRAL_API_KEY`, `GROQ_API_KEY`, and `LLM_<ROLE>` | Reference configuration. |
| `savageops/templates/env.example` | Included legacy OpenAI settings | Replaced with multi-provider keys and chains | Template configuration. |
| `deploy/supabase_migration_phase2.sql` | Did not exist | Migration for `agent_runs` telemetry columns and `model_usage_stats` SQL view | Schema migration for LLM usage tracking. |
| `savageops/templates/supabase_migration.sql` | Phase 9 index migration only | Added phase 2 columns and view | Keep local templates updated. |
| `tests/test_llm_layer.py` | Did not exist | 13 comprehensive unit tests for chains, rate limiter, prompt-size guard, failover, telemetry, grep check, and CLI | Phase 2 test suite. |
| `tests/test_health_and_metrics.py` | Checked `"openai" in res` | Checks `"llm_gemini"`, `"llm_mistral"`, `"llm_groq"` in health diagnostics | Match new multi-provider health checks. |
| `reports/.phase-2-start` | Did not exist | Recorded start commit SHA (`455093d4dfa296f9c003ec0442ca2e088111db2c`) | Tracking phase start. |

---

## 3. Evidence

### Exit Criterion 1: Chain Parsing & Defaults
- Verified parsing of default chains for all four roles (`triage`, `analyst`, `fixer`, `spokesperson`).
- Verified custom override via `LLM_<ROLE>`.
- Verified unknown provider raises `RuntimeError("Unknown LLM provider ...")`.
- Verified missing API key raises `RuntimeError("Missing required environment variable: ...")` naming only the variable.
- Command: `python -m pytest tests/test_llm_layer.py -k "test_parse_chain"`
- Status: **PASS (executed)**

### Exit Criterion 2: Temperature Handling
- Verified `can_pass_temperature` returns `False` for reasoning and temperature-rejecting models (`o1`, `o3`, `o4`, `gpt-5`, `devstral`).
- Verified temperature parameter is omitted for models rejecting temperature.
- Command: `python -m pytest tests/test_llm_layer.py -k "test_temperature"`
- Status: **PASS (executed)**

### Exit Criterion 3: Prompt-Size Guard & Rate Limiter
- Verified prompt-size guard detects prompt size exceeding provider TPM (e.g. Groq 8000 TPM limit) and immediately prefers/advances to next chain entry without crashing.
- Verified sliding-window tracking of RPM and TPM.
- Command: `python -m pytest tests/test_llm_layer.py -k "test_prompt_size_guard"`
- Status: **PASS (executed)**

### Exit Criterion 4: Failover Order & Retry-After
- Verified HTTP 429 failover advances to next model in chain with `fallback_depth = 1`.
- Verified `Retry-After` header is parsed and throttles the failing provider in the limiter.
- Verified 5xx / timeout triggers failover.
- Verified exhausting whole chain raises a clear exception naming the role and all provider errors.
- Command: `python -m pytest tests/test_llm_layer.py -k "test_failover or test_retry or test_all_providers"`
- Status: **PASS (executed)**

### Exit Criterion 5: Structured Output Fallback
- Verified fallback across methods: `default` failure -> `function_calling` succeeds.
- Command: `python -m pytest tests/test_llm_layer.py -k "test_structured_output"`
- Status: **PASS (executed)**

### Exit Criterion 6: Telemetry Persistence
- Verified every LLM call writes `provider`, `model`, `role`, `input_tokens`, `output_tokens`, `latency`, and `fallback_depth` to `agent_runs` and Prometheus metrics.
- Command: `python -m pytest tests/test_llm_layer.py -k "test_telemetry"`
- Status: **PASS (executed)**

### Exit Criterion 7: Grep Test (No Direct LLM Clients in Agents)
- Verified scan across `savageops/agents/*.py` confirms zero direct calls to `ChatOpenAI(`, `OpenAI(`, `create_chat_model(`, or `ChatAnthropic(`. All agents interact strictly via `get_llm(role)`.
- Command: `python -m pytest tests/test_llm_layer.py -k "test_grep"`
- Status: **PASS (executed)**

### Exit Criterion 8: Full Test Suite Execution (Offline, Network Blocked)

```powershell
Pre-flight: no real-looking key remains
.............................................................            [100%]
============================= slowest 5 durations =============================
0.36s call     tests/test_health_and_metrics.py::test_health_run_checks_structure
0.22s call     tests/test_cli.py::test_cli_init
0.20s call     tests/test_cli.py::test_cli_help
0.20s call     tests/test_fixer_safe_failures.py::test_bad_hunk_draft_sets_needs_human_and_cannot_push[empty-llm-hunks]
0.19s call     tests/test_audit_requirements.py::test_run_push_ci_failure_retries_at_most_once
61 passed in 3.45s
```
Status: **PASS (executed)**

### Exit Criterion 9: Live-Provider Checks (User-Run Required)

Per instruction, live provider requests with real keys were not run by the agent.

| Item | Status | Action Required |
|---|---|---|
| `savageops models` | **BLOCKED (user-run)** | Run `python -m savageops.cli models` in your terminal with your real API keys loaded in the session. |
| `savageops doctor` | **BLOCKED (user-run)** | Run `python -m savageops.cli doctor` in your terminal with your real API keys loaded in the session. |
| Live provider pings | **BLOCKED (user-run)** | Tested during `savageops doctor` (`max_tokens=5` per configured provider). |

**Instructions for User**:
In your PowerShell terminal where your real API keys (`GEMINI_API_KEY`, `MISTRAL_API_KEY`, `GROQ_API_KEY`) are set:
1. Run:
   ```powershell
   python -m savageops.cli models
   ```
   **What to paste back**: Only the model names returned under each provider heading and whether any chain entries were flagged. (Do NOT paste any keys).
2. Run:
   ```powershell
   python -m savageops.cli doctor
   ```
   **What to paste back**: The `[OK]` / `[FAIL]` status lines for `llm_gemini`, `llm_mistral`, `llm_groq`. (Do NOT paste any keys).

---

## 4. Failed or Not Achieved

| Item | Status | Root Cause | Impact | Next Step |
|---|---|---|---|---|
| Live provider verification (`models`, `doctor`) | BLOCKED (user-run) | Safety constraint: agent must never run live calls with real keys | Pending user terminal run | User runs CLI commands in their terminal and pastes back non-secret output |

---

## 5. Deviations from the Plan and Decisions Taken

1. **Dynamic Rate Limiter Configuration**:
   - `get_limiter(provider)` was enhanced to read `GROQ_TPM`, `GEMINI_TPM`, etc. dynamically from environment overrides rather than fixing values strictly at module import time.
2. **Backward Compatibility for Agent Monkeypatching**:
   - Agent modules (`triage.py`, `analyst.py`, `fixer.py`, `spokesperson.py`) retain module-level `def get_llm(): return _get_llm_chain(...)` delegating to `savageops.llm.get_llm(...)`. This maintains complete backward compatibility with existing tests that mock `agent.get_llm`.
3. **Safety Isolation**:
   - Zero access or modification to `.env`. Only `.env.example` and `savageops/templates/env.example` were updated.

---

## 6. New Risks Introduced
- None. All 61 tests pass completely hermetic and offline under network blocking fixtures in 3.45 seconds.

---

## 7. Git and Tool Status
- **Branch**: `antigravity/phase-2`
- **Phase Start SHA**: `455093d4dfa296f9c003ec0442ca2e088111db2c`
- **Ruff Lint Check**:
  ```
  .venv\Scripts\ruff check .
  All checks passed!
  ```
- **Pytest Summary**:
  ```
  61 passed in 3.45s
  ```

---

## 8. Gate Recommendation

**GATE: PROCEED (pending user-run live check verification)**
*Reason*: All code, multi-provider chains, rate limiting, structured output fallbacks, metrics, telemetry, and mock unit tests are implemented and passing 61/61 tests offline. Live provider check items are marked `BLOCKED (user-run)` awaiting user confirmation.
