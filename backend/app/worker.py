"""Background SLA monitor: approval reminders/escalations, missed deadlines and outbox retries.
Runs as a daemon thread inside the API process (single instance by design for the prototype)."""

import logging
import threading
import time

from app.config import get_settings
from app.db import session_scope

log = logging.getLogger("opspilot.worker")
_stop = threading.Event()
last_sweep: dict = {"at": None, "actions": 0, "error": None}


def sweep_once() -> int:
    from app.clock import now
    from app.orchestrator import workflow
    from app.services import outbox

    with session_scope() as db:
        n = workflow.escalate_due(db) + outbox.retry_due(db)
    last_sweep.update(at=now().isoformat(), actions=n, error=None)
    return n


def _loop() -> None:
    interval = get_settings().sla_sweep_seconds
    while not _stop.is_set():
        try:
            sweep_once()
        except Exception as e:  # keep the monitor alive; surface the error on /readyz
            log.exception("SLA sweep failed")
            last_sweep["error"] = str(e)
        _stop.wait(interval)


def start() -> None:
    _stop.clear()
    threading.Thread(target=_loop, name="sla-monitor", daemon=True).start()


def stop() -> None:
    _stop.set()
    time.sleep(0)
