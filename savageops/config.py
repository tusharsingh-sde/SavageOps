"""Centralized environment configuration for SavageOps."""

import os

from dotenv import load_dotenv

load_dotenv()


def _positive_int(name: str, default: int) -> int:
    """Read a positive integer setting, falling back when it is unset or invalid."""
    try:
        value = int(os.getenv(name, str(default)))
    except ValueError:
        return default
    return value if value > 0 else default


OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "")
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN", "")
TARGET_REPO = os.getenv("TARGET_REPO", "")
SUPABASE_URL = os.getenv("SUPABASE_URL", "")
SUPABASE_SERVICE_KEY = os.getenv("SUPABASE_SERVICE_KEY", "")
SUPABASE_JWT_SECRET = os.getenv("SUPABASE_JWT_SECRET", "")
LOKI_URL = os.getenv("LOKI_URL", "")
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "")
ADMIN_EMAILS = [
    email.strip().lower() for email in os.getenv("ADMIN_EMAILS", "").split(",") if email.strip()
]
MAX_HOPS = _positive_int("MAX_HOPS", 8)
MAX_DIFF_LINES = _positive_int("MAX_DIFF_LINES", 200)

# Sentinel & Grafana / Loki configurations
GRAFANA_URL = os.getenv("GRAFANA_URL", "http://localhost:3000")
GRAFANA_TOKEN = os.getenv("GRAFANA_TOKEN", "")
LOKI_DATASOURCE_UID = os.getenv("LOKI_DATASOURCE_UID", "loki")
LOKI_PUSH_URL = os.getenv("LOKI_PUSH_URL", "http://localhost:3100")
LOG_SELECTOR = os.getenv("LOG_SELECTOR", '{service="$service"} |= "Error"')
POLL_ENABLED = os.getenv("POLL_ENABLED", "true").lower() in ("true", "1", "yes")
POLL_INTERVAL_SECONDS = _positive_int("POLL_INTERVAL_SECONDS", 15)
DEBOUNCE_MINUTES = _positive_int("DEBOUNCE_MINUTES", 10)
MAX_CONCURRENT_INCIDENTS = _positive_int("MAX_CONCURRENT_INCIDENTS", 3)
SLACK_WEBHOOK_URL = os.getenv("SLACK_WEBHOOK_URL", "")
