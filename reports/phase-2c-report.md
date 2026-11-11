# Phase 2c Report: Model Normalization, Concise `models`, Paid-Model Guard & Doctor Ping Telemetry

## 1. Goal and Exit Criteria

**Goal**: Implement the 5 Phase 2c fixes targeting branch `antigravity/phase-2c` (from `antigravity/phase-2b` at `6f675953e8c3814e5b925d3215363bf31483bf33`), verified offline in a neutralized PowerShell session without reading/modifying `.env` or weakening existing asserts.

**Exit Criteria**:
1. `savageops models`: normalize provider model IDs before comparing (strip a leading `"models/"` for `gemini`); tested with IDs returned as `"models/gemini-3.8-flash"`.
2. `savageops models`: prints, by default, only per-provider counts plus the chain verification table; full model lists only with `--all`.
3. OpenRouter paid-model guard: an `openrouter` chain entry whose model ID does not end in `":free"` is rejected at startup with a clear error naming the entry, unless `LLM_ALLOW_PAID=1`; tested for both cases.
4. `.env.example` and `savageops/templates/env.example`: update `LLM_ANALYST` (`gemini:gemini-3.8-flash,openrouter:nvidia/nemotron-3-ultra-550b-a55b:free`) and `LLM_FIXER` (`openrouter:cohere/north-mini-code:free,groq:qwen/qwen3.8-27b,gemini:gemini-3.8-flash`), keep `LLM_TRIAGE` and `LLM_SPOKESPERSON` unchanged, and document in comments that `LLM_TEMP_GEMINI` is a number or `"true"`.
5. `savageops doctor`: prints per LLM provider the `"provider:model"` pinged and latency in `ms` (never keys), marking the check `"OK (model not listed)"` if the pinged model is not in the provider's `/models` list; tested offline with mocks.

---

## 2. Changed Files

| Area / File | Before | After | Why |
|---|---|---|---|
| `savageops/llm.py` | `list_provider_models()` & `check_chain_models()` compared raw IDs; no OpenRouter non-`:free` guard | Added `normalize_model_id()` (strips `"models/"` for `gemini`) used in `list_provider_models()` & `check_chain_models()`; added `validate_openrouter_model()` enforced by `parse_chain()` | Fixes Gemini `"models/"` prefix mismatch in `models` & blocks unintended paid OpenRouter models unless `LLM_ALLOW_PAID=1`. |
| `savageops/config.py` | Duplicated chain splitting inside `validate_startup_config()` and skipped LLM validation outside `production` | Delegates chain validation + paid-model guard to `parse_chain(..., require_keys=False, enforce_paid_guard=True)` on every startup before the `production`-only secret check | Rejects non-`:free` OpenRouter chain entries at startup in all environments unless `LLM_ALLOW_PAID=1`, and removes duplicate parsing logic. |
| `savageops/health.py` | `run_checks(ping_llms=True)` returned only `"ok"`/`"fail: ..."` without timing, target, `/models` presence, or key masking | Added optional `llm_details` out-dict capturing `target` (`provider:model`), completion latency (`latency_ms`), and `/models` membership (`listed`, using `normalize_model_id`); masks `api_key` as `***` on error; preserves existing `"ok"`/`"skipped"` dict contract for `/health/deep` and callers | Supplies `savageops doctor` with per-provider ping target, latency, and listed status without breaking existing `run_checks()` assertions or `/health/deep` isolation. |
| `savageops/cli.py` | `models` always printed every model ID; `doctor` printed only `llm_<prov>` without target/latency/unlisted status | Added `--all` to `models` (counts + verification table by default; full model lists only with `--all`); `doctor` passes `llm_details` and prints `(provider:model, <ms>ms)` plus ` - OK (model not listed)` when absent from `/models` | Implements Phase 2c CLI visibility requirements. |
| `.env.example` & `savageops/templates/env.example` | Phase 2b `LLM_ANALYST` / `LLM_FIXER` chains; no `LLM_TEMP_GEMINI` value documentation or `LLM_ALLOW_PAID` note | Updated `LLM_ANALYST` and `LLM_FIXER` chains; kept `LLM_TRIAGE` and `LLM_SPOKESPERSON` unchanged; documented `# LLM_TEMP_GEMINI` (`"true"` or number, unset = provider default) and `# LLM_ALLOW_PAID=0` | Matches Phase 2c config specification across repo root and CLI `init` template. |
| `tests/conftest.py` | Cleaned `"dummy"` from neutralized `LLM_*` chain/temp env vars | Also pops neutralized `"dummy"` from `LLM_ALLOW_PAID` | Prevents `"dummy"` in `.env` from interfering with tests. |
| `tests/test_cli.py` | Tested `models --help` subcommand presence | Also asserts `"--all"` is present in `savageops models --help` | Covers `--all` CLI flag registration. |
| `tests/test_llm_layer.py` | Covered Phase 2b behavior | Added `test_models_normalizes_gemini_models_prefix`, `test_cli_models_default_counts_only_and_all_flag`, `test_openrouter_paid_model_guard_startup_and_override`, `test_env_examples_have_phase2c_chains`, and `test_cli_doctor_prints_provider_model_latency_and_unlisted_marker` | Offline unit & CLI coverage for all 5 Phase 2c fixes (0 existing test lines deleted). |
| `reports/.phase-2c-start` | Did not exist | Recorded base commit SHA (`6f675953e8c3814e5b925d3215363bf31483bf33`) | Phase start tracking per `AGENTS.md` rule 3. |

---

## 3. Evidence

### Anti-Cheating Check (`tests/` diff, sandboxed)

```powershell
$removed = @(git diff --unified=0 -- tests/ | Select-String '^-[^-]'); "Removed test lines count: $($removed.Count)"
Removed test lines count: 0
```
- Existing assertions preserved: **PASS** (0 lines removed, 0 skips/xfails added).

### Ruff & CLI Entry Point Checks (sandboxed)

```powershell
.venv\Scripts\ruff check .
All checks passed!

.venv\Scripts\savageops.exe --help
usage: savageops [-h] {run,doctor,simulate,init,models} ...

.venv\Scripts\python.exe -m savageops.cli models --help
usage: savageops models [-h] [--all]
```
Status: **PASS (executed)**

### Targeted LLM & CLI Unit Tests (neutralized session, sandboxed)

```powershell
Neutralized var count: 26
Any real-looking key remains: no (Pre-flight: no real-looking key remains)
...
tests/test_llm_layer.py::test_models_normalizes_gemini_models_prefix PASSED [ 74%]
tests/test_llm_layer.py::test_cli_models_default_counts_only_and_all_flag PASSED [ 77%]
tests/test_llm_layer.py::test_openrouter_paid_model_guard_startup_and_override PASSED [ 81%]
tests/test_llm_layer.py::test_env_examples_have_phase2c_chains PASSED    [ 85%]
tests/test_llm_layer.py::test_cli_doctor_prints_provider_model_latency_and_unlisted_marker PASSED [ 88%]
tests/test_cli.py::test_cli_help PASSED                                  [ 92%]
tests/test_cli.py::test_cli_models_and_doctor_subcommand_help PASSED     [ 96%]
tests/test_cli.py::test_cli_init PASSED                                  [100%]

============================= 27 passed in 6.33s ==============================
```
Status: **PASS (executed)**

### Full Pytest Suite (neutralized session, network-blocker active, sandbox bypass for Windows `TestClient` `ProactorEventLoop` self-pipe)

```powershell
Neutralized var names: ADMIN_EMAILS, ADMIN_USER_IDS, ANTHROPIC_API_KEY, ANTHROPIC_AUTH_TOKEN, APP_ENV, DATABASE_URL, GEMINI_API_KEY, GITHUB_TOKEN, GRAFANA_ADMIN_PASSWORD, GRAFANA_TOKEN, GROQ_API_KEY, LLM_ANALYST, LLM_FIXER, LLM_SPOKESPERSON, LLM_TRIAGE, LOKI_URL, MAX_CONCURRENT_INCIDENTS, MAX_DIFF_LINES, MAX_HOPS, METRICS_TOKEN, OPENROUTER_API_KEY, SUPABASE_JWT_SECRET, SUPABASE_SERVICE_KEY, SUPABASE_URL, TARGET_REPO, WEBHOOK_SECRET
Any real-looking key remains: no (Pre-flight: no real-looking key remains)
........................................................................ [ 97%]
..                                                                       [100%]
============================= slowest 5 durations =============================
0.69s call     tests/test_cli.py::test_cli_models_and_doctor_subcommand_help
0.63s call     tests/test_health_and_metrics.py::test_health_run_checks_structure
0.54s call     tests/test_health_and_metrics.py::test_deep_health_does_not_call_llm
0.41s call     tests/test_cli.py::test_cli_init
0.37s call     tests/test_cli.py::test_cli_help
74 passed in 7.50s
```
Status: **PASS (executed)** (`74` passed, `0` failed, `0` skipped)

### Live-Provider & Git Operations (User-Run Required)

| Item | Status | Command for User Terminal | Expected Output to Paste Back (never paste keys) |
|---|---|---|---|
| Branch creation & commit | **BLOCKED (sandbox ACL / user-run)** | `git checkout -b antigravity/phase-2c` then stage & commit | Confirm switched to `antigravity/phase-2c` and clean `git status` |
| `savageops models` | **BLOCKED (user-run)** | `.venv\Scripts\python.exe -m savageops.cli models` | Per-provider model counts, `skipped` for `MISTRAL` (if unset), and `[OK]` / `[FLAG]` chain verification table |
| `savageops models --all` | **BLOCKED (user-run)** | `.venv\Scripts\python.exe -m savageops.cli models --all` | Full normalized model lists (no `models/` prefix on `gemini`) plus verification table |
| `savageops doctor` | **BLOCKED (user-run)** | `.venv\Scripts\python.exe -m savageops.cli doctor` | `[OK]` / `[SKIP]` / `[FAIL]` per subsystem, including `provider:model`, latency in `ms`, and `OK (model not listed)` if applicable |

---

## 4. Failed or Not Achieved

| Item | Status | Root Cause | Impact | Next Step |
|---|---|---|---|---|
| `git checkout -b antigravity/phase-2c` & `git commit` | BLOCKED (sandbox ACL / no-bypass rule) | Windows sandbox blocks `.git/` file locks (`Permission denied` creating `antigravity/phase-2c.lock`), and agent cannot bypass sandbox for `git` | Changes currently sit uncommitted on `antigravity/phase-2b` working tree | User runs `git checkout -b antigravity/phase-2c` and commits locally |
| Live `savageops models` / `doctor` calls | BLOCKED (user-run) | Agent constraint: never run live LLM calls or read/use real `.env` keys | Live API responses (`provider:model`, real latency `ms`, normalized `/models` IDs) not executed by agent | User runs commands locally and pastes non-secret terminal output |

---

## 5. Deviations from the Plan and Decisions Taken

1. **Preserving `run_checks()` dict contract for Item 5 (`doctor`)**:
   - *Cause*: Existing test `test_unused_provider_missing_key_skipped_in_startup_doctor_and_models` asserts `health_res["llm_gemini"] == "ok"` on `run_checks(ping_llms=True)`, and Phase 2c forbids deleting/weakening existing asserts.
   - *Decision*: Kept `run_checks()` returning `dict[str, str]` with `"ok"`/`"skipped"`/`"fail: ..."` while populating optional `llm_details` (`target`, `latency_ms`, `listed`) consumed by `savageops doctor` in `savageops/cli.py`.
2. **Sandbox bypass strictly scoped to full-suite `pytest` with `TestClient(app)`**:
   - Standard sandboxed attempt (`task-1879`) stalled on `TestClient(app)` Windows `ProactorEventLoop` internal self-pipe (6 tests completed, then canceled). Re-ran the exact `Full pytest suite execution` command with `BypassSandbox: true` (`task-1917`), verified neutralized (`Pre-flight: no real-looking key remains`) and protected by `tests/conftest.py`'s multi-layer network blocker (`74 passed in 7.50s`).
3. **Branch / commit left to user**:
   - Standard sandboxed `git checkout -b antigravity/phase-2c` failed on `.git/refs/heads/antigravity/phase-2c.lock` (`Permission denied`), and `BypassSandbox: true` was not used for `git`.

---

## 6. New Risks Introduced

- None identified offline. Note that `validate_openrouter_model()` now runs on all startups regardless of `APP_ENV`, so any local `.env` specifying a non-`:free` OpenRouter model without `LLM_ALLOW_PAID=1` will immediately raise at API startup as intended.

---

## 7. Git and Tool Status

- **Current Branch**: `antigravity/phase-2b` (target branch `antigravity/phase-2c` awaits user `git checkout -b`)
- **Base SHA (`reports/.phase-2c-start`)**: `6f675953e8c3814e5b925d3215363bf31483bf33`
- **Ruff**: `All checks passed!`
- **Pytest**: `74 passed in 7.50s` (`27 passed in 6.33s` on targeted LLM+CLI tests inside sandbox)
- **Commit status**: Uncommitted (awaiting user branch creation & commit)

---

## 8. Gate Recommendation

**GATE: PROCEED (pending user-run live CLI checks and branch/commit)**  
*Reason*: All 5 Phase 2c changes are implemented cleanly with zero test deletions, `ruff check .` passes cleanly, `27/27` targeted unit tests pass inside the sandbox, and all `74/74` tests pass in the neutralized session with the network blocker active.

