"""Centralized environment configuration for SavageOps."""

import os

from dotenv import find_dotenv, load_dotenv

_env_path = find_dotenv(usecwd=True)
if _env_path:
    load_dotenv(_env_path)


def _positive_int(name: str, default: int) -> int:
    """Read a positive integer setting, falling back when it is unset or invalid."""
    try:
        value = int(os.getenv(name, str(default)))
    except ValueError:
        return default
    return value if value > 0 else default


GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
MISTRAL_API_KEY = os.getenv("MISTRAL_API_KEY", "")
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "")

LLM_TRIAGE = os.getenv("LLM_TRIAGE", "gemini:gemini-2.5-flash-lite,gemini:gemini-2.5-flash")
LLM_ANALYST = os.getenv(
    "LLM_ANALYST", "gemini:gemini-2.5-flash,openrouter:openai/gpt-oss-120b:free"
)
LLM_FIXER = os.getenv("LLM_FIXER", "openrouter:qwen/qwen3-coder:free,gemini:gemini-2.5-flash")
LLM_SPOKESPERSON = os.getenv(
    "LLM_SPOKESPERSON", "groq:openai/gpt-oss-120b,gemini:gemini-2.5-flash-lite"
)
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN", "")
TARGET_REPO = os.getenv("TARGET_REPO", "")
SUPABASE_URL = os.getenv("SUPABASE_URL", "")
SUPABASE_SERVICE_KEY = os.getenv("SUPABASE_SERVICE_KEY", "")
SUPABASE_JWT_SECRET = os.getenv("SUPABASE_JWT_SECRET", "")
LOKI_URL = os.getenv("LOKI_URL", "")
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "")
METRICS_TOKEN = os.getenv("METRICS_TOKEN", "")
ADMIN_EMAILS = [
    email.strip().lower() for email in os.getenv("ADMIN_EMAILS", "").split(",") if email.strip()
]
ADMIN_USER_IDS = [
    user_id.strip() for user_id in os.getenv("ADMIN_USER_IDS", "").split(",") if user_id.strip()
]
MAX_HOPS = _positive_int("MAX_HOPS", 12)
MAX_DIFF_LINES = _positive_int("MAX_DIFF_LINES", 200)

# Sentinel & Grafana / Loki configurations
GRAFANA_URL = os.getenv("GRAFANA_URL", "http://localhost:3000")
GRAFANA_TOKEN = os.getenv("GRAFANA_TOKEN", "")
LOKI_DATASOURCE_UID = os.getenv("LOKI_DATASOURCE_UID", "loki")
LOKI_PUSH_URL = os.getenv("LOKI_PUSH_URL", "http://localhost:3100")
LOG_SELECTOR = os.getenv("LOG_SELECTOR", '{service="$service"}')
POLL_ENABLED = os.getenv("POLL_ENABLED", "true").lower() in ("true", "1", "yes")
POLL_INTERVAL_SECONDS = _positive_int("POLL_INTERVAL_SECONDS", 15)
DEBOUNCE_MINUTES = _positive_int("DEBOUNCE_MINUTES", 10)
MAX_CONCURRENT_INCIDENTS = _positive_int("MAX_CONCURRENT_INCIDENTS", 3)
SLACK_WEBHOOK_URL = os.getenv("SLACK_WEBHOOK_URL", "")
DATABASE_URL = os.getenv("DATABASE_URL", "")
APP_ENV = os.getenv("APP_ENV", "development").lower()
SERVICE_PATH_MAP = os.getenv("SERVICE_PATH_MAP", "")
MAX_DAILY_LLM_CALLS = _positive_int("MAX_DAILY_LLM_CALLS", 500)
MAX_DAILY_LLM_TOKENS = _positive_int("MAX_DAILY_LLM_TOKENS", 1_000_000)
GROUP_COOLDOWN_SECONDS = _positive_int("GROUP_COOLDOWN_SECONDS", 60)


def validate_startup_config() -> None:
    """Validate LLM chains/paid guard on startup, and required secrets in production."""
    from savageops.llm import DEFAULT_CHAINS, PROVIDERS, parse_chain

    used_providers: list[str] = []
    for role in DEFAULT_CHAINS:
        for prov, _ in parse_chain(role, require_keys=False, enforce_paid_guard=True):
            if prov not in used_providers:
                used_providers.append(prov)

    if APP_ENV != "production":
        return

    required = {
        "GITHUB_TOKEN": GITHUB_TOKEN,
        "TARGET_REPO": TARGET_REPO,
        "SUPABASE_URL": SUPABASE_URL,
        "SUPABASE_SERVICE_KEY": SUPABASE_SERVICE_KEY,
        "SUPABASE_JWT_SECRET": SUPABASE_JWT_SECRET,
        "WEBHOOK_SECRET": WEBHOOK_SECRET,
        "METRICS_TOKEN": METRICS_TOKEN,
        "DATABASE_URL": DATABASE_URL,
    }
    missing = [name for name, value in required.items() if not value]
    if not ADMIN_USER_IDS:
        missing.append("ADMIN_USER_IDS")

    # Validate provider keys ONLY for providers referenced in configured LLM_* chains
    for prov in used_providers:
        key_name = PROVIDERS[prov]["env_key"]
        if not os.getenv(key_name, "").strip() and key_name not in missing:
            missing.append(key_name)

    if missing:
        raise RuntimeError("Missing required production configuration: " + ", ".join(missing))
