# Phase 2b Report: OpenRouter, Colon-Safe Parsing, Temperature Policy & Health Check Isolation

## 1. Goal and Exit Criteria

**Goal**: Extend the Phase 2 LLM layer on branch `antigravity/phase-2b` with colon-safe chain parsing, the `openrouter` provider (`20` RPM, `50/day` per-process cap, failover on 429/daily-limit), unused-provider skipping (startup/doctor/models never fail when an unused provider lacks a key), configurable per-role temperature handling (with Gemini omitting temperature unless `LLM_TEMP_GEMINI` is set, `devstral` allowed, and HTTP 400 retry-without-temp caching), strict separation of `/health/deep` (zero LLM calls) from `savageops doctor` (`max_tokens=5` once per configured provider) and `savageops models` (`/models` listing only), updated `.env.example` / `savageops/templates/env.example`, and comprehensive offline unit tests.

**Exit Criteria**:
1. Colon-safe `provider:model` parsing splitting on FIRST colon only; tests for model IDs with 0, 1, and 2 colons.
2. Provider `openrouter` (`https://openrouter.ai/api/v1`, `OPENROUTER_API_KEY`, default `20` RPM, per-process `50/day` request cap); HTTP 429 or daily-limit error fails over to next chain entry.
3. Provider required ONLY if referenced in some `LLM_*` chain; missing key for unused provider (e.g. `mistral`) shows `"skipped"` and never fails startup, `doctor`, or `models`.
4. Temperature handling: remove `"devstral"` from `REJECT_TEMPERATURE_PREFIXES`; `LLM_TEMPERATURE_<ROLE>` (defaults: `fixer` `0.1`, `triage` `0.1`, `analyst` `0.2`, `spokesperson` `0.7`); `gemini` defaults to omitting temperature unless `LLM_TEMP_GEMINI` is set; HTTP 400 rejecting temperature retries once without it and remembers per model for the process.
5. `/health/deep` makes ZERO LLM calls; `doctor` pings once per configured provider with `max_tokens=5`; `models` lists `/models` without consuming completion quota.
6. `python -m savageops.cli models|doctor` and `savageops` console entry point both work.
7. `.env.example` and `savageops/templates/env.example` updated with current chains, `GEMINI_API_KEY`, `GROQ_API_KEY`, `OPENROUTER_API_KEY`, and optional commented `# MISTRAL_API_KEY`. `.env` untouched.
8. Ruff clean, all mock/offline tests passing in neutralized session; live provider runs marked `BLOCKED (user-run)`.

---

## 2. Changed Files

| Area / File | Before | After | Why |
|---|---|---|---|
| `savageops/llm.py` | Only `gemini`/`mistral`/`groq`; `devstral` rejected temperature; no daily per-provider cap; no HTTP 400 temperature retry/cache | Added `openrouter` (`20` RPM, `50/day` cap + daily-limit failover); updated `DEFAULT_CHAINS`; removed `"devstral"` from `REJECT_TEMPERATURE_PREFIXES`; added `DEFAULT_TEMPERATURES`, `resolve_temperature`, `LLM_TEMP_GEMINI`, HTTP 400 retry-once & `_temperature_rejected_models`; added `get_used_providers()` & `get_primary_model_for_provider()` | Core Phase 2b LLM policies and OpenRouter integration. |
| `savageops/config.py` | Missing `OPENROUTER_API_KEY`; old chain defaults; checked `mistral` even if unused | Added `OPENROUTER_API_KEY`; updated default `LLM_*` chains; `validate_startup_config()` splits on first colon and requires keys ONLY for providers appearing in `LLM_*` chains | Startup passes when unused provider (e.g. `mistral`) has no key. |
| `savageops/agents/spokesperson.py` | Hardcoded `temperature=0.7` in `_get_llm_chain` | Delegates temperature resolution to `get_llm("spokesperson", schema=SavageSummary)` | Honors `LLM_TEMPERATURE_SPOKESPERSON` (default `0.7`) and Gemini provider-default rule uniformly. |
| `savageops/health.py` | `run_checks()` unconditionally pinged LLMs on every call, including `/health/deep` | `run_checks(ping_llms: bool = False)` separates static status (`/health/deep`, zero LLM calls) from `ping_llms=True` (`doctor` only: ONE `max_tokens=5` ping per configured provider; `"skipped"` when unused & missing key) | Prevents `/health/deep` from consuming LLM quota and avoids failing unused providers. |
| `savageops/cli.py` | Treated `"skipped"` as `[FAIL]` exit 1; called `list_provider_models()` twice; missing `"skipped"` for unused providers | Added `[SKIP]` handling and allowed `"skipped"`/`"configured"` in `doctor`; reused `models_by_provider` in `models` and printed `skipped` for unused providers without keys | Clean CLI output and zero failures for unused providers. |
| `.env.example` | Contained active `MISTRAL_API_KEY` and older model chains | Updated chains (`gemini`, `openrouter`, `groq`), `OPENROUTER_API_KEY`, optional commented `# MISTRAL_API_KEY`, and commented temperature options | Reference config aligned with Phase 2b. |
| `savageops/templates/env.example` | Same legacy keys and chains | Identical updates matching `.env.example` | Packaged CLI `init` template updated. |
| `tests/conftest.py` | Only set `APP_ENV`, `SAVAGEOPS_MOCK`, `PYTHON_DOTENV_DISABLED`, `POLL_ENABLED` | Clears `"dummy"` from neutralized `LLM_*` chain/temp vars so default chains apply during tests in neutralized `.env` sessions | Allows full test suite to run cleanly when `.env` defines `LLM_*`. |
| `tests/test_health_and_metrics.py` | Asserted `llm_gemini`, `llm_mistral`, `llm_groq` in `run_checks()` | Preserved all existing asserts, added `llm_openrouter`, added `test_deep_health_does_not_call_llm` | Proves `/health/deep` makes zero LLM calls while preserving `llm_*` checks. |
| `tests/test_cli.py` | Checked `--help` for `doctor`, `simulate`, `init` | Preserved existing asserts, added `models` assert and `test_cli_models_and_doctor_subcommand_help` | Verifies `python -m savageops.cli models|doctor` entry point. |
| `tests/test_llm_layer.py` | Tested Phase 2 defaults and `devstral` rejection | Updated expectations to Phase 2b defaults; added tests for 0/1/2 colons, OpenRouter `50/day` cap + daily-limit failover, unused `mistral` skipped across startup/doctor/models, temperature defaults/Gemini omission/400 retry, and single-ping `doctor` | Offline mock coverage for all Phase 2b rules. |
| `reports/.phase-2b-start` | Did not exist | Recorded start commit SHA (`97b314b67f5bdf829bc71a6f2f540d38379e23b9`) | Phase start tracking per AGENTS.md rule 3. |

---

## 3. Evidence

### Criterion 1-4, 6-8: Offline Unit Tests (Neutralized Session, Sandboxed & Full Suite)

**`tests/test_llm_layer.py` (sandboxed, neutralized)**:
```powershell
Pre-flight: no real-looking key remains
...................                                                      [100%]
============================= slowest 5 durations =============================
1.04s call     tests/test_llm_layer.py::test_unused_provider_missing_key_skipped_in_startup_doctor_and_models
0.86s setup    tests/test_llm_layer.py::test_parse_chain_defaults
0.07s call     tests/test_llm_layer.py::test_cli_models_check
0.06s call     tests/test_llm_layer.py::test_temperature_http_400_retry_and_process_cache
0.05s call     tests/test_llm_layer.py::test_openrouter_provider_config_and_50_day_cap_failover
19 passed in 4.32s
```
Status: **PASS (executed)**

**`tests/test_cli.py` (sandboxed, neutralized)**:
```powershell
Pre-flight: no real-looking key remains
...                                                                      [100%]
============================= slowest 5 durations =============================
0.98s call     tests/test_cli.py::test_cli_models_and_doctor_subcommand_help
0.79s setup    tests/test_cli.py::test_cli_help
0.56s call     tests/test_cli.py::test_cli_init
0.52s call     tests/test_cli.py::test_cli_help
0.02s setup    tests/test_cli.py::test_cli_init
3 passed in 2.93s
```
Status: **PASS (executed)**

**`tests/test_health_and_metrics.py` (neutralized, network-blocker active)**:
```powershell
Pre-flight: no real-looking key remains
.....                                                                    [100%]
============================= slowest 5 durations =============================
0.63s call     tests/test_health_and_metrics.py::test_health_run_checks_structure
0.55s call     tests/test_health_and_metrics.py::test_deep_health_does_not_call_llm
0.01s call     tests/test_health_and_metrics.py::test_sentinel_webhook_endpoint

(2 durations < 0.005s hidden.  Use -vv to show these durations.)
5 passed in 4.14s
```
Status: **PASS (executed)**

**Full Suite (neutralized, network-blocker active)**:
```powershell
Pre-flight: no real-looking key remains
.....................................................................    [100%]
============================= slowest 5 durations =============================
0.68s call     tests/test_cli.py::test_cli_models_and_doctor_subcommand_help
0.64s call     tests/test_health_and_metrics.py::test_health_run_checks_structure
0.57s call     tests/test_health_and_metrics.py::test_deep_health_does_not_call_llm
0.47s call     tests/test_audit_requirements.py::test_run_push_ci_failure_retries_at_most_once
0.41s call     tests/test_cli.py::test_cli_init
69 passed in 7.69s
```
Status: **PASS (executed)**

**Ruff Lint Check**:
```powershell
.venv\Scripts\ruff check .
All checks passed!
```
Status: **PASS (executed)**

**CLI Entry Points (`--help` verified)**:
```powershell
.venv\Scripts\python.exe -m savageops.cli --help
.venv\Scripts\savageops.exe --help
usage: savageops [-h] {run,doctor,simulate,init,models} ...
```
Status: **PASS (executed)**

### Live-Provider Checks (User-Run Required)

Live calls against Gemini, Groq, and OpenRouter were **NOT** run by the agent.

| Item | Status | Command for User Terminal | Expected Output to Paste Back |
|---|---|---|---|
| `savageops models` | **BLOCKED (user-run)** | `.venv\Scripts\python.exe -m savageops.cli models` | Returned model IDs per provider, `skipped` for unused `MISTRAL`, and `[OK]` / `[FLAG]` lines for chains (never keys). |
| `savageops doctor` | **BLOCKED (user-run)** | `.venv\Scripts\python.exe -m savageops.cli doctor` | `[OK]` / `[SKIP]` / `[FAIL]` lines for `llm_gemini`, `llm_mistral`, `llm_groq`, `llm_openrouter` (never keys). |
| Live provider pings (`max_tokens=5`) | **BLOCKED (user-run)** | Executed inside `savageops doctor` above | Confirm ONE `max_tokens=5` ping per configured provider succeeded (`[OK]`). |

---

## 4. Failed or Not Achieved

| Item | Status | Root Cause | Impact | Next Step |
|---|---|---|---|---|
| Live `savageops models` & `savageops doctor` | BLOCKED (user-run) | Agent constraint: never run live LLM calls or touch real keys | Live provider IDs and ping connectivity unverified until user executes locally | User runs `.venv\Scripts\python.exe -m savageops.cli models` and `.venv\Scripts\python.exe -m savageops.cli doctor` in their PowerShell terminal and pastes back non-secret results |

---

## 5. Deviations from the Plan and Decisions Taken

1. **Neutralization of `LLM_*` vars in `tests/conftest.py`**:
   - *Cause*: Neutralizing `.env` sets every variable defined in `.env` (now including `LLM_TRIAGE`, etc.) to the literal string `"dummy"`, which would trigger `"Invalid chain format in LLM_TRIAGE: 'dummy'"` in tests inspecting defaults.
   - *Decision*: `tests/conftest.py` pops exact `"dummy"` values from `LLM_*` chain and temperature env vars so default chains and temperatures remain cleanly testable in neutralized sessions.
2. **Sandbox Bypass Commands**:
   - `git checkout -b antigravity/phase-2b` required user-approved `BypassSandbox: true` after sandboxed run hit `.git` read-only AppContainer ACL (`Permission denied`).
   - `.venv\Scripts\python.exe -m pytest tests/test_health_and_metrics.py` and full-suite pytest run required `BypassSandbox: true` (after sandboxed run hung on `TestClient(app)` Windows `ProactorEventLoop` self-pipe, with neutralization pre-flight verified and `tests/conftest.py` network blocker active).
   - No `git commit` was run (user will commit manually).
3. **Scope Hygiene**:
   - Pre-existing 1-line trailing whitespace diffs in `deploy/supabase_migration_phase2.sql` and `reports/phase-2-report.md` were left untouched.

---

## 6. New Risks Introduced

- None identified offline. Note that OpenRouter's `20` RPM / `50/day` limiters are per-process in-memory (`ProviderRateLimiter`), matching the Phase 2b per-process requirement.

---

## 7. Git and Tool Status

- **Branch**: `antigravity/phase-2b`
- **Phase Start SHA**: `97b314b67f5bdf829bc71a6f2f540d38379e23b9`
- **Ruff**: `All checks passed!`
- **Pytest**: `69 passed in 7.69s`
- **Commit status**: Uncommitted (awaiting user commit per prompt instruction).

---

## 8. Gate Recommendation

**GATE: PROCEED (pending user-run live provider verification and user git commit)**  
*Reason*: All Phase 2b features (colon-safe parsing, OpenRouter 20 RPM / 50-per-day failover, unused provider skipping, temperature rules & HTTP 400 cache/retry, `/health/deep` isolation, `.env.example` updates) are verified offline with `ruff` passing and `69 passed in 7.69s`. Live `models`/`doctor` checks await user terminal run.

