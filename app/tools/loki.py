"""Log retrieval tool with Loki API and fixture fallback mode."""

import time
from pathlib import Path

import httpx

from app.config import LOKI_URL


def get_logs(service: str, minutes: int = 15) -> str:
    """Retrieve error logs for a service, falling back to local fixtures if LOKI_URL is unset."""
    if not LOKI_URL:
        # Fixture mode: read local sample logs
        fixture_file = Path(__file__).resolve().parents[2] / "fixtures" / "sample_logs.txt"
        if fixture_file.exists():
            return fixture_file.read_text(encoding="utf-8")
        return ""

    end_ns = int(time.time() * 1e9)
    start_ns = end_ns - minutes * 60 * int(1e9)
    resp = httpx.get(
        f"{LOKI_URL}/loki/api/v1/query_range",
        params={
            "query": f'{{service="{service}"}} |= "Error"',
            "start": start_ns,
            "end": end_ns,
            "limit": 200,
        },
        timeout=15.0,
    )
    resp.raise_for_status()
    result_data = resp.json().get("data", {}).get("result", [])
    lines = [entry[1] for stream in result_data for entry in stream.get("values", [])]
    return "\n".join(lines)
