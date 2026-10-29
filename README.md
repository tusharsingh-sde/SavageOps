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
    <a href="https://www.python.org/downloads/"><img alt="Python 3.10+" src="https://img.shields.io/badge/python-3.10%2B-blue?style=flat-square&logo=python&logoColor=white" /></a>
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
- [Sentinel (Chowkidar)](#-sentinel-chowkidar)
- [Hardening & CI Loop](#-hardening--ci-loop)
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
                                                                    Agent 3 (Fixer)
                                                                     Code draft + diff
                                                                               ▼
                                                                    ✅ GATE 2: Push / Revise / Reject
                                                                               ▼
                                                                    Branch → PR → CI check loop
```

### Agents

| # | Agent | Role | Temp | Details |
|---|-------|------|:----:|---------|
| 1 | **Triage** | Log parsing, file/line identification, severity P1–P4 | `0` | LogQL injection protection; Grafana proxy or Loki direct |
| 2 | **Analyst** | Root cause analysis, git blame, `fast_fix` + `permanent_fix` | `0` | Proposes two solutions; loops back to Triage if context is insufficient |
| 3a | **Spokesperson** | Savage summary for Gate 1 | `0.7` | Roasts the code pattern, explains root cause, estimates cloud burn rate |
| 3b | **Fixer** | Code drafting, AST + Ruff lint, PR & CI loop | `0` | Validates syntax, computes diff, opens PR, auto-retries on test failure |

---

## ⚡ Quick Start

### Prerequisites

| Requirement | Minimum |
|-------------|---------|
| Python | 3.10+ |
| OpenAI API key | `gpt-4o-mini` or higher |
| GitHub PAT | `repo` scope |
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

### 3. Verify Health & Dependencies

```bash
savageops doctor
```

This runs a comprehensive connectivity check against Supabase, GitHub, OpenAI,
Grafana, and Loki.

### 4. Run the Server

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
| `savageops run [--host HOST] [--port PORT]` | Start FastAPI server, background Sentinel poller, and watchdog |
| `savageops doctor` | Run comprehensive dependency health check |
| `savageops simulate [--service SVC] [--count N]` | Push synthetic error traces to Loki to trigger real Grafana alerts |
| `savageops init` | Scaffold `savageops-config/` with compose file, `.env.example`, and Grafana provisioning |

---

## 🔐 Environment Variables

Create a `.env` file from `.env.example`. All variables are read via
`savageops/config.py`.

| Variable | Required | Default | Description |
|----------|:--------:|---------|-------------|
| `OPENAI_API_KEY` | ✅ | — | OpenAI API key |
| `OPENAI_MODEL` | | `gpt-4o-mini` | Model used by all agents |
| `GITHUB_TOKEN` | ✅ | — | GitHub PAT with `repo` scope |
| `TARGET_REPO` | ✅ | — | Target repo as `owner/repo` |
| `SUPABASE_URL` | ✅ | — | Supabase project URL |
| `SUPABASE_SERVICE_KEY` | ✅ | — | Supabase service role key |
| `SUPABASE_JWT_SECRET` | ✅ | — | JWT secret for webhook auth |
| `LOKI_URL` | ✅ | — | Loki query URL |
| `WEBHOOK_SECRET` | ✅ | — | Bearer token for Grafana → SavageOps webhook |
| `ADMIN_EMAILS` | | — | Comma-separated list of admin emails |
| `MAX_HOPS` | | `8` | Max agent loop iterations |
| `MAX_DIFF_LINES` | | `200` | Max diff lines per fix |
| `GRAFANA_URL` | | `http://localhost:3000` | Grafana base URL |
| `GRAFANA_TOKEN` | | — | Grafana Service Account token |
| `LOKI_DATASOURCE_UID` | | `loki` | Grafana datasource UID for Loki |
| `LOKI_PUSH_URL` | | `http://localhost:3100` | Loki push API endpoint |
| `LOG_SELECTOR` | | `{service="$service"} \|= "Error"` | LogQL selector template |
| `POLL_ENABLED` | | `true` | Enable backup Grafana poller |
| `POLL_INTERVAL_SECONDS` | | `15` | Poller interval in seconds |
| `DEBOUNCE_MINUTES` | | `10` | Alert debounce window |
| `MAX_CONCURRENT_INCIDENTS` | | `3` | Max simultaneous agent pipelines |
| `SLACK_WEBHOOK_URL` | | — | Slack incoming webhook for notifications |

---

## 🛡️ Sentinel (Chowkidar)

Sentinel is the alert-ingestion gateway sitting between Grafana and the AI agents:

| Feature | Description |
|---------|-------------|
| **Deduplication** | Dedupes by `fingerprint:startsAt` episode key |
| **Debouncing** | Flapping alerts within `DEBOUNCE_MINUTES` increment `occurrences` instead of triggering redundant AI pipelines |
| **Backup Poller** | Polls Grafana Alertmanager every 15 s in case webhooks drop |
| **Heartbeat Watchdog** | Auto-creates an incident if Grafana is unreachable for 3 consecutive poll cycles |
| **Concurrency Control** | Limits simultaneous active agent pipelines to `MAX_CONCURRENT_INCIDENTS` (default 3) |

---

## 🧪 Hardening & CI Loop

| Layer | Mechanism |
|-------|-----------|
| **Path Safety** | Blocks path traversal (`..`, leading `/` or `\`), `.github/` workflows; restricts auto-fix files to `.py` |
| **Pre-PR Validation** | AST parse (`ast.parse()`) + Ruff lint (`ruff check --select E9,F`) |
| **Post-PR CI Verification** | Queries GitHub Actions check runs on the PR HEAD; on failure, Fixer analyses the test output and pushes a retry commit |
| **Budget Enforcers** | `info_requests ≤ 2`, `rejections ≤ 2`, `revisions ≤ 2`, `MAX_HOPS ≤ 8` |

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
stuck in intermediate states.

---

## 🐳 Docker & Grafana Provisioning

One-command setup with Grafana, Loki, and SavageOps:

```bash
cd deploy

# 1. Start the observability stack
docker compose up -d loki grafana

# 2. (Optional) Create a Grafana Service Account Viewer token → GRAFANA_TOKEN in .env

# 3. Build and start SavageOps
docker compose up -d --build savageops
```

Pre-configured provisioning is included in `deploy/grafana/provisioning/`:

- **Datasources** — Loki pre-configured with proxy UID `loki`
- **Contact Point & Policy** — Sends firing alerts to
  `http://savageops:8000/api/webhook/grafana` with Bearer auth
- **Alert Rules** — Fires when error log rate > 3 per minute

---

## 🗄️ Supabase Database Schema

Run `deploy/supabase_migration_phase9.sql` in your Supabase SQL Editor to add
Sentinel columns:

```sql
ALTER TABLE incidents ADD COLUMN IF NOT EXISTS group_key      text;
ALTER TABLE incidents ADD COLUMN IF NOT EXISTS occurrences    int  DEFAULT 1;
ALTER TABLE incidents ADD COLUMN IF NOT EXISTS source         text;
ALTER TABLE incidents ADD COLUMN IF NOT EXISTS last_seen_at   timestamptz DEFAULT now();
```

---

## 🔬 Test Suite

```bash
# Unit & integration tests
pytest tests/ -v

# Canary evaluation
python scripts/canary.py
```

| Test Module | Coverage |
|-------------|----------|
| `test_sentinel_ingest.py` | Deduplication, debouncing, poller fallback |
| `test_ci_loop_and_path_safety.py` | Path traversal blocking, AST validation, CI retry logic |
| `test_grafana_loki_tools.py` | Grafana proxy, Loki queries |
| `test_health_and_metrics.py` | `/health` endpoint, Prometheus metric export |
| `test_cli.py` | CLI entry-point registration and subcommands |

---

## 📂 Project Structure

```text
SavageOps/
├── savageops/                  # Installable package (primary source)
│   ├── agents/                 #   Triage, Analyst, Spokesperson, Fixer
│   ├── sentinel/               #   Ingest, Poller, Runner, Webhook
│   ├── tools/                  #   GitHub, Grafana, Loki integrations
│   ├── templates/              #   Scaffolding for `savageops init`
│   ├── fixtures/               #   Sample alerts & logs
│   ├── cli.py                  #   CLI entry-point
│   ├── config.py               #   Centralized env config
│   ├── db.py                   #   Supabase client
│   ├── graph.py                #   LangGraph orchestration
│   ├── main.py                 #   FastAPI application factory
│   ├── models.py               #   Pydantic schemas
│   ├── metrics.py              #   Prometheus metric definitions
│   ├── simulate.py             #   Synthetic error trace generator
│   ├── watchdog.py             #   Background incident watchdog
│   └── webhook.py              #   Webhook route handler
├── deploy/                     # Docker Compose + Grafana provisioning
├── scripts/                    # Canary & utility scripts
├── tests/                      # Pytest test suite
├── fixtures/                   # Root-level sample data
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
4. Write tests in `tests/` for any new behaviour.
5. Run the full suite: `pytest tests/ -v`
6. Open a PR — a human (and maybe a Spokesperson agent 🤬) will review it.

---

## License

MIT
