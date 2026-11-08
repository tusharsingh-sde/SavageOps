PHASE 1 - Baseline audit (read-only)
Map the repo; run ruff and pytest; verify claims from potential_bugs_report.md and e2e_report.md against the code: mock fallback only via an explicit env flag and never silent in production; /metrics scrape token; CI-failure loop has max retries, a timeout and handles "no CI configured"; hunk fixer tests (0 matches, 2+ matches, empty hunks, invalid syntax after apply); redaction tests (key, token, email, phone in prompt, agent_runs and PR body); gate validation and per-incident lock; webhook secret fail-closed with hmac.compare_digest; poller and rules filter on savageops=true; LogQL keeps traceback frames. Only code change allowed: git rm -r app/ (old duplicate), after proving nothing imports it.
Exit: claim-vs-proof table complete, every item VERIFIED / UNVERIFIED / WRONG with file:line.

PHASE 2 - LLM layer
1. savageops/llm.py: get_llm(role) for roles triage, analyst, fixer, spokesperson. Read LLM_<ROLE> as an ordered chain of provider:model. Providers (OpenAI-compatible): gemini (https://generativelanguage.googleapis.com/v1beta/openai/, GEMINI_API_KEY), mistral (https://api.mistral.ai/v1, MISTRAL_API_KEY), groq (https://api.groq.com/openai/v1, GROQ_API_KEY). Defaults for .env.example (verify IDs with `savageops models`): LLM_TRIAGE=gemini:gemini-3.5-flash-lite,mistral:mistral-small-latest ; LLM_ANALYST=gemini:gemini-3.8-flash,mistral:mistral-large-latest ; LLM_FIXER=mistral:devstral-2-2512,gemini:gemini-3.8-flash ; LLM_SPOKESPERSON=groq:openai/gpt-oss-120b,gemini:gemini-3.5-flash-lite. Missing key or unknown provider = clear startup error naming only the variable.
2. Failover on HTTP 429/5xx/timeout or structured-output failure (default method, then function_calling, then json_mode + Pydantic validation), honoring Retry-After. If the whole chain fails the incident ends failed with a clear reason, never hangs. Do not pass temperature to models that reject it.
3. Per-provider rate limiter (RPM/TPM, groq 8000 TPM) and a prompt-size guard that prefers the next chain entry over exceeding TPM.
4. Every LLM call writes provider, model, role, input/output tokens, latency, fallback_depth to agent_runs (migration file) and to /metrics.
5. `savageops models` lists model IDs returned by each configured provider (names only) and flags chain entries not in the list. `savageops doctor` pings every configured provider with max_tokens=5.
6. Remove all OpenAI usage and OPENAI_* settings; update .env.example and templates/env.example; a grep test fails if any agent builds a client outside get_llm().
7. No eval harness. Add SQL view model_usage_stats over agent_runs grouped by role, provider, model: calls, avg latency, input/output tokens, avg fallback_depth, failures.
8. Mock-based unit tests for chain parsing, failover order, 429/Retry-After, structured-output fallback, size guard, metrics.
Exit: ruff and pytest green with real summary lines; doctor pings each configured provider live (BLOCKED if I have not set keys); agent_runs rows carry provider/model/tokens/latency.

PHASE 3 - Live E2E, alert to Gate 1
docker compose up, doctor, simulate, one incident through the real chains with MAX_CONCURRENT_INCIDENTS=1.
Exit: exactly one incident (webhook + poller must not duplicate); Triage file_path and line range match the real traceback; AnalysisReport and savage summary are in incidents.pending; agent_runs shows the model used per role. Show raw SQL output.

PHASE 4 - Live E2E, Gate 1 to PR
Approve (admin JWT from session env vars) -> draft diff, prove nothing exists on GitHub yet -> Gate 2 push -> PR on TARGET_REPO; observe the CI loop or the no-CI case.
Exit: PR URL, minimal diff, PR targets the default branch, no direct push to main, nothing merged.

PHASE 5 - Failure runs
Wrong-gate body rejected; double /respond rejected; container restart with a pending Gate 1 incident (Postgres checkpointer) still resumes; wrong webhook secret + poller still creates an incident with source=poller; 50 distinct alerts with the same alertname+service increment occurrences on ONE open incident and never exceed MAX_CONCURRENT_INCIDENTS pipelines; Grafana stopped for 3 poll cycles creates one GrafanaUnreachable incident and restart creates no extras; empty WEBHOOK_SECRET refuses to start.
Exit: raw output per case; system ends safely in needs_human / rejected / failed.

PHASE 6 - Security pass
Secret scan of the working tree and full git history (install a scanner if possible, else a regex script; never print matched values); redaction test; /metrics and /health/deep auth; ADMIN_USER_IDS is a UUID; branch protection state of TARGET_REPO verified read-only. Produce a checklist for ME: rotate Supabase service key, GitHub token and any key an earlier agent read; disable public signups in Supabase Auth.
Exit: scan clean, checklist written.

PHASE 7 - Docs and demo
README (quick start: init, doctor, run, simulate), architecture diagram (Mermaid), 3-minute demo script, known limits, and reports/FINAL.md consolidating all phase reports: total changed, total not achieved, remaining risks.
Exit: a clean machine can bring the stack up from the README alone.
