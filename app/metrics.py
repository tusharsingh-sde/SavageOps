"""Prometheus metrics registry and telemetry instrumentation for SavageOps."""

import functools
import time

from prometheus_client import Counter, Gauge, Histogram

# Metric Definitions
INGEST = Counter(
    "sentinel_ingest_total", "Total alerts processed by Sentinel", ["source", "result"]
)
AGENT_RUNS = Counter("agent_runs_total", "Total agent step executions", ["agent", "kind"])
AGENT_LAT = Histogram("agent_latency_seconds", "Execution latency for each agent", ["agent"])
ESCALATIONS = Counter("incident_escalations_total", "Total incidents escalated to needs_human")
OPEN_INC = Gauge(
    "incidents_open", "Count of currently open incidents by lifecycle status", ["status"]
)


def timed(agent: str):
    """Decorator to measure and record execution latency of agent tasks."""

    def decorator(fn):
        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            start = time.time()
            try:
                return fn(*args, **kwargs)
            finally:
                AGENT_LAT.labels(agent=agent).observe(time.time() - start)

        return wrapper

    return decorator
