"""Concurrency control runner for launching the SavageOps multi-agent pipeline."""

import logging
import threading

from savageops.config import MAX_CONCURRENT_INCIDENTS
from savageops.db import log_run, update_incident
from savageops.graph import run_pipeline

logger = logging.getLogger(__name__)

_sem = threading.BoundedSemaphore(MAX_CONCURRENT_INCIDENTS)


def start(incident_id: str, alert: dict):
    """Execute the multi-agent pipeline within the concurrency-limiting semaphore."""
    with _sem:
        # Phase 10: Check if alert is still firing before invoking expensive LLM pipeline
        try:
            from savageops.tools.grafana import still_firing

            fp = alert.get("fingerprint")
            if fp and not still_firing(fp):
                logger.info(
                    "Sentinel: Alert %s already resolved before agent startup; marking resolved.",
                    fp,
                )
                update_incident(incident_id, status="resolved")
                return
        except Exception as e:
            logger.debug("Sentinel: could not check still_firing: %s", e)

        try:
            logger.info("Sentinel: Starting multi-agent pipeline for incident %s", incident_id)
            run_pipeline(incident_id, alert)
        except Exception as e:
            logger.error("Sentinel: Pipeline execution failed for %s: %s", incident_id, e)
            log_run(incident_id, "pipeline", "error", error=str(e))
            update_incident(incident_id, status="failed", pending={"error": str(e)})
