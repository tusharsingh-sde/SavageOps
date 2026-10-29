"""Daily / CI Canary verification script testing Triage and Analyst agents against known bugs."""

import json
import logging
import sys
from datetime import date
from importlib import resources

from savageops.agents.analyst import run_analyst
from savageops.agents.triage import run_triage
from savageops.db import update_incident
from savageops.sentinel.ingest import ingest, normalize
from savageops.watchdog import send_alert

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("canary")


def run_canary() -> bool:
    """Execute end-to-end canary check using sample fixture to test agent intelligence without pushing code."""
    try:
        fixture_text = (resources.files("savageops") / "fixtures" / "sample_alert.json").read_text(
            encoding="utf-8"
        )
    except (FileNotFoundError, ModuleNotFoundError):
        logger.error("Canary: packaged fixtures/sample_alert.json not found")
        return False
    data = json.loads(fixture_text)

    alert = normalize(data["alerts"][0])
    alert["fingerprint"] = f"canary-{date.today().isoformat()}"
    inc_id = ingest(alert, "canary")
    if not inc_id:
        logger.info("Canary: Alert already processed for today.")
        return True

    try:
        triage_report = run_triage(inc_id, alert)
        ok = (
            "app.py" in triage_report.file_path
            and triage_report.severity in ("P1", "P2", "P3", "P4")
        )
        if ok:
            analyst_res = run_analyst(inc_id, triage_report.model_dump())
            ok = bool(
                analyst_res.analysis
                and analyst_res.analysis.fast_fix
                and analyst_res.analysis.permanent_fix
            )

        if not ok:
            send_alert("CANARY FAIL: Agents output quality regression or unexpected response.")
            return False

        logger.info("Canary test passed successfully.")
        return True
    except Exception as e:
        send_alert(f"CANARY FAIL with exception: {e}")
        return False
    finally:
        update_incident(inc_id, status="canary")


if __name__ == "__main__":
    success = run_canary()
    sys.exit(0 if success else 1)
