<p align="center">
  <h1 align="center">🔥 SavageOps</h1>
  <p align="center">
    <strong>A brutally honest, multi-agent AI DevOps & SRE incident auto-remediation system.</strong>
  </p>
  <p align="center">
    Triages production incidents · Roasts offending code · Opens reviewed GitHub PRs — with humans in the loop.
  </p>
  <p align="center">
    <a href="#-quick-start"><img alt="Quick Start" src="https://img.shields.io/badge/-Quick_Start-brightgreen?style=for-the-badge" /></a>
    <a href="https://www.python.org/downloads/"><img alt="Python 3.11+" src="https://img.shields.io/badge/python-3.11%2B-blue?style=flat-square&logo=python&logoColor=white" /></a>
    <a href="#license"><img alt="MIT License" src="https://img.shields.io/badge/license-MIT-green?style=flat-square" /></a>
  </p>
</p>

---

## Table of Contents

- [Why SavageOps?](#-why-savageops)
- [Architecture](#-architecture)
- [Agents](#agents)
- [Quick Start](#-quick-start)
- [CLI Commands](#-cli-commands)
- [Environment Variables](#-environment-variables)
- [Multi-Provider LLM Layer](#-multi-provider-llm-layer)
- [Sentinel (Chowkidar)](#-sentinel-chowkidar)
- [Hardening & CI Loop](#-hardening--ci-loop)
- [Security & Redaction](#-security--redaction)
- [Observability & Metrics](#-observability--metrics)
- [Docker & Grafana Provisioning](#-docker--grafana-provisioning)
- [Database Schema](#-supabase-database-schema)
- [Test Suite](#-test-suite)
- [Project Structure](#-project-structure)
- [Contributing](#-contributing)
- [License](#license)

---

## 💡 Why SavageOps?

On-call engineers get woken up at 3 AM, stare at walls of logs, trace through
five microservices, find that someone forgot a `None` check, push a hotfix, and
go back to sleep — only to do it all over again next week.

**SavageOps automates the boring parts** and keeps humans in the loop for the
decisions that matter:

- **⚡ Seconds, not hours** — AI agents triage, analyse, and draft a fix before
  your coffee is ready.
- **🛡️ Two human gates** — No code ships without your explicit approval.
- **🤬 Savage honesty** — The Spokesperson agent roasts the bad code pattern so
  the lesson sticks (and the post-mortem writes itself).
- **🔄 Self-healing CI** — If the PR breaks tests, SavageOps reads the failure
  output and pushes a retry commit automatically.
- **🔀 Multi-provider LLM failover** — Chains across Gemini, Groq, and
  OpenRouter with automatic fallback, rate limiting, and token telemetry.

---

## 🏗️ Architecture

```text
Grafana ── webhook (Bearer token) ──▶ ┐
                                       ├──▶ Sentinel Ingest: dedupe → debounce → insert
Poller (backup every 15 s) ──────────▶ ┘       │
                                                ▼
                                      Semaphore Runner (max 3 concurrent)
                                                │
              ┌─────────────────────────────────┼──────────────────────────────────┐
              ▼                                 ▼                                  ▼
     Agent 1 (Triage)             Agent 2 (Analyst)                    Agent 3 (Spokesperson)
      Log parsing &                Root cause &                         Savage summary &
      severity P1–P4              fix proposals                         Gate 1 approval
              │                         │                                      │
              └── InfoRequest ──────────┘                                      │
                                        └────── UserFeedback ──────────────────┘
                                                                               ▼
                                                                    ✅ GATE 1: Approve / Reject
                                                                               ▼
                                                                    Agent 4 (Fixer)
                                                                     Code draft + diff
                                                                               ▼
                                                                    ✅ GATE 2: Push / Revise / Reject
                                                                               ▼
                                                                    Branch → PR → CI check loop
```

### Agents

| # | Agent | Role | Temp | Details |
|---|-------|------|:----:|---------:|
| 1 | **Triage** | Log parsing, file/line identification, severity P1–P4 | `0` | Smart repo-relative path extraction; LogQL injection protection; Grafana proxy or Loki direct |
| 2 | **Analyst** | Root cause analysis, git blame, `fast_fix` + `permanent_fix` | `0` | Proposes two solutions with invariant-checked evidence; loops back to Triage if context is insufficient |
| 3a | **Spokesperson** | Savage summary for Gate 1 | `0.7` | Roasts the code pattern, explains root cause, estimates cloud burn rate |
| 3b | **Fixer** | Code drafting, AST + Ruff lint, PR & CI loop | `0` | Validates syntax, computes diff, opens PR, auto-retries on test failure (configurable retries + timeout) |

---

## ⚡ Quick Start

### Prerequisites

| Requirement | Minimum |
|-------------|---------|
| Python | 3.11+ |
| LLM API key | At least one of: Gemini, Groq, or OpenRouter |
| GitHub PAT | `repo` scope (classic) or fine-grained with Contents + PRs |
| Supabase project | URL + Service Key |
| Grafana + Loki | Local or remote (Docker setup included) |

### 1. Clone & Install

```bash
git clone https://github.com/<your-org>/SavageOps.git
cd SavageOps

python -m venv .venv

# Linux / macOS
source .venv/bin/activate

# Windows PowerShell
.\.venv\Scripts\Activate.ps1

pip install -e .
```

### 2. Configure Environment

```bash
cp .env.example .env
# Edit .env with your API keys, tokens, and URLs
```

> **Tip:** See the [Environment Variables](#-environment-variables) section for a
> full reference of every supported setting.

### 3. Verify LLM Providers & Model Chains

```bash
savageops models
```

This lists available models from each configured provider (Gemini, Groq,
OpenRouter) and verifies that every agent's LLM chain resolves to a live model.
Fix any `[FLAG]` entries before proceeding.

### 4. Verify Health & Dependencies

```bash
savageops doctor
```

This runs a comprehensive connectivity check against Supabase, GitHub, Gemini,
Groq, OpenRouter, Grafana, and Loki — including live latency probes for each
LLM provider.

### 5. Run the Server

```bash
savageops run --host 127.0.0.1 --port 8000
```

- 🩺 Health check: <http://127.0.0.1:8000/health>
- 📖 Swagger UI: <http://127.0.0.1:8000/docs>
- 📊 Prometheus metrics: <http://127.0.0.1:8000/metrics>

---

## 💻 CLI Commands

The `savageops` CLI is registered as a first-class entry-point:

```text
savageops <command> [options]
```

| Command | Description |
|---------|-------------|
| `savageops run [--host HOST] [--port PORT] [--workers N]` | Start FastAPI server, background Sentinel poller, and watchdog |
| `savageops doctor` | Run comprehensive dependency health check with LLM latency probes |
| `savageops models [--all]` | List model IDs from configured LLM providers and verify agent chains |
| `savageops simulate [--service SVC] [--count N]` | Push synthetic error traces to Loki to trigger real Grafana alerts |
| `savageops init` | Scaffold `savageops-config/` with compose file, `.env.example`, and Grafana provisioning |

---

## 🔐 Environment Variables

Create a `.env` file from `.env.example`. All variables are read via
`savageops/config.py`.

### LLM Provider Keys

| Variable | Required | Description |
|----------|:--------:|-------------|
| `GEMINI_API_KEY` | ✅ ¹ | Google Gemini API key |
| `GROQ_API_KEY` | | Groq API key (optional fallback) |
| `OPENROUTER_API_KEY` | | OpenRouter API key (optional fallback) |

> ¹ At least one provider key is required. Gemini is the recommended primary.

### Agent Model Chains

Each agent has a configurable failover chain in `provider:model` format,
comma-separated. The agent tries each entry in order until one succeeds.

| Variable | Default |
|----------|---------|
| `LLM_TRIAGE` | `gemini:gemini-2.5-flash-lite,gemini:gemini-2.5-flash` |
| `LLM_ANALYST` | `gemini:gemini-2.5-flash,openrouter:openai/gpt-oss-120b:free` |
| `LLM_FIXER` | `openrouter:qwen/qwen3-coder:free,gemini:gemini-2.5-flash` |
| `LLM_SPOKESPERSON` | `groq:openai/gpt-oss-120b,gemini:gemini-2.5-flash-lite` |

### Core Configuration

| Variable | Required | Default | Description |
|----------|:--------:|---------|-------------|
| `GITHUB_TOKEN` | ✅ | — | GitHub PAT with `repo` scope |
| `TARGET_REPO` | ✅ | — | Target repo as `owner/repo` |
| `SUPABASE_URL` | ✅ | — | Supabase project URL |
| `SUPABASE_SERVICE_KEY` | ✅ | — | Supabase service role key |
| `SUPABASE_JWT_SECRET` | ✅ | — | JWT secret for Supabase admin auth |
| `LOKI_URL` | ✅ | — | Loki query URL |
| `WEBHOOK_SECRET` | ✅ | — | Bearer token for Grafana → SavageOps webhook |
| `METRICS_TOKEN` | | — | Bearer token to protect `/metrics` endpoint |
| `ADMIN_USER_IDS` | ✅ | — | Comma-separated Supabase Auth user UUID allowlist |
| `ADMIN_EMAILS` | | — | Comma-separated admin emails (case-insensitive) |

### Pipeline Tuning

| Variable | Default | Description |
|----------|---------|-------------|
| `MAX_HOPS` | `12` | Max agent loop iterations |
| `MAX_DIFF_LINES` | `200` | Max diff lines per fix |
| `MAX_DAILY_LLM_CALLS` | `500` | Per-process daily ceiling on LLM requests |
| `MAX_DAILY_LLM_TOKENS` | `1000000` | Conservative daily token reservation cap per process |
| `MAX_CONCURRENT_INCIDENTS` | `3` | Max simultaneous agent pipelines |

### Sentinel & Grafana / Loki

| Variable | Default | Description |
|----------|---------|-------------|
| `GRAFANA_URL` | `http://localhost:3000` | Grafana base URL |
| `GRAFANA_TOKEN` | — | Grafana Service Account token |
| `LOKI_DATASOURCE_UID` | `loki` | Grafana datasource UID for Loki |
| `LOKI_PUSH_URL` | `http://localhost:3100` | Loki push API endpoint |
| `LOG_SELECTOR` | `{service="$service"}` | Unfiltered LogQL stream selector |
| `POLL_ENABLED` | `true` | Enable backup Grafana poller |
| `POLL_INTERVAL_SECONDS` | `15` | Poller interval in seconds |
| `GROUP_COOLDOWN_SECONDS` | `60` | Cooldown after an alert group closes |
| `DEBOUNCE_MINUTES` | `10` | Alert debounce window in minutes |

### Infrastructure

| Variable | Default | Description |
|----------|---------|-------------|
| `DATABASE_URL` | — | Supabase direct/session PostgreSQL URL for durable graph checkpoints (never transaction pooler) |
| `APP_ENV` | `development` | Set to `production` to require durable checkpoints and admin IDs |
| `SERVICE_PATH_MAP` | `{}` | JSON service→repo mapping, e.g. `{"billing":{"repo":"org/billing","prefix":"src"}}` |
| `SLACK_WEBHOOK_URL` | — | Slack incoming webhook for P1 notifications |
| `GRAFANA_ADMIN_PASSWORD` | — | Password for the local Grafana admin account (Docker) |
| `SAVAGEOPS_MOCK` | — | Set to `1` to allow fixture fallback when Loki/Grafana are unconfigured |

---

## 🔀 Multi-Provider LLM Layer

SavageOps uses a **provider-agnostic LLM layer** (`savageops/llm.py`) with:

| Feature | Description |
|---------|-------------|
| **Failover chains** | Each agent role has a comma-separated chain of `provider:model` entries; on failure, the next entry is tried |
| **3 providers** | `gemini` (Google Gemini), `groq` (Groq Cloud), `openrouter` (OpenRouter) |
| **Rate limiting** | Thread-safe per-provider rate limiters with daily call + token budgets |
| **Token telemetry** | Input/output tokens, latency, and fallback depth logged per agent run |
| **Temperature policy** | Per-role temperature defaults (`0` for Triage/Analyst/Fixer, `0.7` for Spokesperson) with rejection limits |
| **Paid guard** | Configurable guard against accidentally using paid models in development |

Verify your setup with:

```bash
savageops models       # Check chain resolution
savageops doctor       # Live latency probes for each provider
```

---

## 🛡️ Sentinel (Chowkidar)

Sentinel is the alert-ingestion gateway sitting between Grafana and the AI agents:

| Feature | Description |
|---------|-------------|
| **Deduplication** | Deterministic fingerprint hashing with normalized timestamps; one open incident per alert/service group |
| **Debouncing** | Open groups and a configurable post-resolution cooldown suppress duplicate AI pipelines |
| **Backup Poller** | Polls Grafana Alertmanager every 15 s in case webhooks drop; safe label parsing with non-dict guards |
| **Heartbeat Watchdog** | Auto-creates an incident if Grafana is unreachable for 3 consecutive poll cycles |
| **Concurrency Control** | Semaphore-based runner limits simultaneous active agent pipelines to `MAX_CONCURRENT_INCIDENTS` (default 3) |
| **Webhook Security** | Strict case-insensitive Bearer parsing, alert payload validation (422 on null/non-list), non-dict item skipping |

---

## 🧪 Hardening & CI Loop

| Layer | Mechanism |
|-------|-----------:|
| **Path Safety** | Blocks path traversal, auth/configuration, migrations, tests, and `.github`; restricts auto-fix files to `.py` |
| **Smart Path Extraction** | Repo-relative path resolution: filters external frames (site-packages, venv), strips container roots, finds source root directories |
| **Pre-PR Validation** | AST parse (`ast.parse()`) + Ruff lint (`ruff check --select E9,F`) |
| **Post-PR CI Verification** | Queries GitHub Actions check runs on the PR HEAD with configurable grace period for Actions registration; on failure, Fixer analyses the test output and pushes a retry commit |
| **Budget Enforcers** | `info_requests ≤ 2`, `rejections ≤ 3`, `revisions ≤ 2`, `MAX_HOPS` default 12, plus per-process daily request and token caps |
| **Strict Pydantic Validation** | `TriageReport` raises `ValueError` on hallucinated line ranges; `InfoRequest.minutes` bounded 1–1440; `FixedFile.commit_message` requires min_length=1 |

---

## 🔒 Security & Redaction

The `savageops/security.py` module provides deterministic, regex-based
credential redaction before any data is sent to external LLM APIs:

| Pattern | What It Catches |
|---------|-----------------|
| URI passwords | `://user:password@host` → `://user:[REDACTED]@host` |
| Bearer tokens | `Authorization: Bearer <token>` |
| API keys & secrets | Generic `api_key=`, `password=`, `secret=` patterns |
| Cloud provider keys | AWS (`AKIA...`), Gemini (`AIza...`), Groq (`gsk_...`) |
| GitHub tokens | Classic (`ghp_`, `gho_`, `ghu_`, `ghs_`, `ghr_`), fine-grained (`github_pat_`) |
| Slack tokens | `xox[baprs]-...` (with hyphens) |
| JWTs | `eyJ...eyJ...` three-segment base64 tokens |
| Email addresses | RFC-style addresses (with `REDACTED@` exclusion to avoid double-redaction) |
| Phone numbers | 10+ digit sequences (with ISO date negative lookahead to avoid false positives on timestamps) |
| SSNs | `XXX-XX-XXXX` patterns |
| LogQL injection | `_sanitize_logql_value()` strips backticks, braces, and pipe operators from user-supplied labels |

All redaction is applied recursively via `redact_untrusted_data()` which
preserves `dict`, `list`, `tuple`, and `set` types.

---

## 📊 Observability & Metrics

Prometheus metrics are exported at `GET /metrics`:

| Metric | Type | Labels | Description |
|--------|------|--------|-------------|
| `sentinel_ingest_total` | Counter | `source`, `result` | Alerts processed (new / duplicate / debounced) |
| `agent_runs_total` | Counter | `agent`, `kind` | Execution counts per agent |
| `agent_latency_seconds` | Histogram | `agent` | Execution latency per agent |
| `incident_escalations_total` | Counter | — | Incidents escalated to `needs_human` |
| `incidents_open` | Gauge | `status` | Active incidents by lifecycle status |

A background **Watchdog** continuously monitors open incidents and flags any
stuck in intermediate states, with timezone-safe timestamp parsing and
Slack notification with delivery confirmation.

---

## 🐳 Docker & Grafana Provisioning

### Monitoring Stack Only (Recommended for Local Dev)

```bash
cd deploy
docker compose --env-file ../.env up -d grafana loki
```

### Full Stack (Including SavageOps Container)

```bash
cd deploy
docker compose --env-file ../.env up -d --build
```

> **Note:** If you're running `savageops run --port 8000` locally, start only
> `grafana` and `loki` to avoid port clashes. The `savageops` container service
> in the compose file binds to port 8000.

> **Webhook Networking:** When running SavageOps locally alongside Docker, the
> Grafana contact point should use `http://host.docker.internal:8000/api/webhook/grafana`
> as the webhook URL.

Pre-configured provisioning is included in `deploy/grafana/provisioning/`:

- **Datasources** — Loki pre-configured with proxy UID `loki`
- **Contact Point & Policy** — Sends firing alerts to
  `http://savageops:8000/api/webhook/grafana` with Bearer auth
- **Alert Rules** — Fires when error log rate > 3 per minute

---

## 🗄️ Supabase Database Schema

Run `savageops/templates/supabase_migration.sql` in your Supabase SQL Editor.
The migration is fully idempotent (`CREATE TABLE IF NOT EXISTS` +
`ADD COLUMN IF NOT EXISTS`) and safe for both fresh installs and existing setups.

### Tables

```sql
-- incidents: Core incident lifecycle tracking
CREATE TABLE IF NOT EXISTS incidents (
    id text PRIMARY KEY,
    fingerprint text,
    group_key text,
    source text DEFAULT 'webhook',
    status text NOT NULL DEFAULT 'received',
    occurrences int DEFAULT 1,
    alert jsonb,
    triage jsonb,
    analysis jsonb,
    fix_draft jsonb,
    chosen_solution text,
    pr_url text,
    ci_status text,
    pending jsonb,
    last_seen_at timestamptz DEFAULT now(),
    alerted_at timestamptz,
    created_at timestamptz DEFAULT now(),
    updated_at timestamptz DEFAULT now()
);

-- agent_runs: LLM call telemetry per incident
CREATE TABLE IF NOT EXISTS agent_runs (
    id bigint GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
    incident_id text REFERENCES incidents(id) ON DELETE CASCADE,
    agent text, role text, provider text, model text, kind text,
    input jsonb, output jsonb,
    input_tokens int DEFAULT 0,
    output_tokens int DEFAULT 0,
    latency float DEFAULT 0.0,
    fallback_depth int DEFAULT 0,
    error text,
    created_at timestamptz DEFAULT now()
);
```

### Indexes & Views

- **Partial unique index** on `group_key` for open incidents (prevents duplicate pipelines)
- **Status index** on `incidents(status)` for watchdog queries
- **`model_usage_stats` view** — Aggregates call count, avg latency, total tokens, and failure rate per `role/provider/model`

### Setup Notes

- In Supabase Auth, disable public signups and email confirmation bypasses
- Put trusted users' Auth UUIDs in `ADMIN_USER_IDS`
- Protect the target repository's default branch with required human review
- Production uses the **direct/session** Postgres URL for checkpoints — do not use the transaction pooler
- Run **one Uvicorn worker** per SavageOps instance (each process owns a poller and watchdog)

---

## 🔬 Test Suite

```bash
# Full test suite
pytest tests/ -v

# Canary evaluation
python scripts/canary.py
```

### Test Modules (20 files, 122+ tests)

| Test Module | Coverage |
|-------------|----------|
| `test_audit_redaction.py` | Secret redaction across all pattern types |
| `test_audit_requirements.py` | Pydantic model constraints and field validation |
| `test_ci_loop_and_path_safety.py` | Path traversal blocking, AST validation, CI retry logic, `CI_STATUS_TIMEOUT_SECONDS` override, `MAX_DIFF_LINES` overflow |
| `test_cli.py` | CLI entry-point registration, subcommands (`run`, `doctor`, `models`, `simulate`, `init`) |
| `test_contracts_and_security.py` | Security contract enforcement and redaction integration |
| `test_db_resilience.py` | Thread-safe Supabase client, `.single()` fix, canary cache, system-incident skip |
| `test_fixer_safe_failures.py` | Fixer agent failure modes and edge cases |
| `test_gate_response.py` | Gate 1/2 approval and rejection flows |
| `test_github_tools_resilience.py` | PyGithub `sha=None` crash, `CheckRunOutput` AttributeError, CI grace period, multi-repo `repo_name` forwarding, retry assertions |
| `test_grafana_loki_tools.py` | Grafana proxy, Loki queries, LogQL sanitization, fixture fallback, `still_firing` safety |
| `test_graph_resilience.py` | LangGraph orchestration edge cases |
| `test_health_and_metrics.py` | `/health` endpoint, Prometheus metric export, adaptive TTL cache |
| `test_isolation_blocker.py` | Network isolation: blocks httpx, DNS, and raw socket in test environment |
| `test_llm_layer.py` | Multi-provider failover, rate limiting, token telemetry, temperature rejection |
| `test_sentinel_ingest.py` | Deduplication, debouncing, poller fallback, monitoring-down detection |
| `test_step5b_resilience.py` | Config validation, auth case-insensitive Bearer, health checks, main startup/shutdown |
| `test_step5c2_resilience.py` | Smart triage path extraction, deterministic fingerprint, analyst invariant check |
| `test_step6a_resilience.py` | Watchdog timezone parsing, send_alert delivery, runner shutdown, poller safe labels, webhook validation |
| `test_step6b_resilience.py` | Strict Pydantic validation, security token patterns, Loki LogQL sanitization, Grafana non-dict guard |
| `test_step6c_resilience.py` | TriageReport strict ValueError, ISO-safe phone redaction, Loki RuntimeError on real failure, `still_firing` empty fingerprint |

---

## 📂 Project Structure

```text
SavageOps/
├── savageops/                  # Installable package (primary source)
│   ├── agents/                 #   Triage, Analyst, Spokesperson, Fixer
│   │   ├── triage.py           #     Log parsing, severity, smart path extraction
│   │   ├── analyst.py          #     Root cause analysis with invariant checks
│   │   ├── spokesperson.py     #     Savage summary and Gate 1
│   │   └── fixer.py            #     Code drafting, PR, CI loop
│   ├── sentinel/               #   Alert ingestion gateway
│   │   ├── ingest.py           #     Deterministic fingerprint, dedupe, debounce
│   │   ├── poller.py           #     Grafana Alertmanager backup poller
│   │   ├── runner.py           #     Semaphore-based concurrent pipeline runner
│   │   └── webhook.py          #     FastAPI webhook route with strict validation
│   ├── tools/                  #   External service integrations
│   │   ├── github_tools.py     #     PyGithub wrapper (PRs, commits, CI checks)
│   │   ├── grafana.py          #     Grafana alert proxy
│   │   └── loki.py             #     Loki LogQL queries with injection protection
│   ├── templates/              #   Scaffolding for `savageops init`
│   │   ├── supabase_migration.sql  # Idempotent DB schema
│   │   ├── docker-compose.yml      # Template compose
│   │   └── grafana/provisioning/   # Alert rules, datasources, contact points
│   ├── fixtures/               #   Sample alerts & logs (dev/mock mode)
│   ├── auth.py                 #   JWT + Bearer token authentication
│   ├── cli.py                  #   CLI entry-point (run, doctor, models, etc.)
│   ├── config.py               #   Centralized env config with validation
│   ├── db.py                   #   Thread-safe Supabase client
│   ├── graph.py                #   LangGraph orchestration
│   ├── health.py               #   Adaptive TTL health checks with LLM probes
│   ├── llm.py                  #   Multi-provider LLM layer (Gemini/Groq/OpenRouter)
│   ├── main.py                 #   FastAPI application factory with graceful shutdown
│   ├── metrics.py              #   Prometheus metric definitions
│   ├── models.py               #   Pydantic schemas with strict validation
│   ├── security.py             #   Credential redaction & LogQL sanitization
│   ├── simulate.py             #   Synthetic error trace generator
│   └── watchdog.py             #   Background incident monitor with Slack alerts
├── deploy/                     # Docker Compose + Grafana provisioning
│   ├── docker-compose.yml
│   └── grafana/provisioning/
├── scripts/                    # Canary & utility scripts
│   └── canary.py
├── tests/                      # Pytest test suite (20 modules, 122+ tests)
├── .env.example                # Environment template
├── pyproject.toml              # Package metadata & dependencies
├── Dockerfile                  # Container build
└── README.md                   # ← You are here
```

---

## 🤝 Contributing

1. Fork the repo and create a feature branch.
2. Install dev dependencies: `pip install -e .`
3. Run `savageops doctor` to verify your local environment.
4. Run `savageops models` to verify LLM chain resolution.
5. Write tests in `tests/` for any new behaviour.
6. Run the full suite: `pytest tests/ -v`
7. Run lint: `ruff check .`
8. Open a PR — a human (and maybe a Spokesperson agent 🤬) will review it.

---

## License

MIT
