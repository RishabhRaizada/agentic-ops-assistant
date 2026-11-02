"""Admin-editable runtime settings (approval matrix, thresholds, chaos flags) stored in the DB."""

import copy
from typing import Any

from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import Setting

ROLE_RANK = {"analyst": 1, "ops_manager": 2, "regional_head": 3, "director": 4}
ESCALATION_LADDER = ["ops_manager", "regional_head", "director"]

DEFAULTS: dict[str, Any] = {
    # Approval matrix: the first band whose max covers the amount decides the approver role.
    "approval_matrix": [
        {"max_inr": 50000, "role": "ops_manager"},
        {"max_inr": 500000, "role": "regional_head"},
        {"max_inr": None, "role": "director"},
    ],
    # Hours an approver gets by priority, capped by the request's own decision deadline.
    "approval_sla_hours": {"P1": 2, "P2": 4, "P3": 24, "P4": 48},
    "reminder_minutes_before_due": 60,
    "conf_high": None,  # None -> env default
    "conf_block": None,
    "auto_link_duplicate_conf": 0.85,
    "chaos": {
        "inventory_down": False,
        "llm_primary_down": False,
        "llm_all_down": False,
        "mail_down": False,
        "llm_latency_ms": 0,
    },
}


def get(db: Session, key: str) -> Any:
    row = db.get(Setting, key)
    if row is not None:
        return copy.deepcopy(row.value)
    value = copy.deepcopy(DEFAULTS.get(key))
    if key == "conf_high" and value is None:
        value = get_settings().conf_high
    if key == "conf_block" and value is None:
        value = get_settings().conf_block
    return value


def put(db: Session, key: str, value: Any) -> None:
    row = db.get(Setting, key)
    if row is None:
        db.add(Setting(key=key, value=value))
    else:
        row.value = value
    db.flush()


def chaos(db: Session) -> dict[str, Any]:
    return {**DEFAULTS["chaos"], **(get(db, "chaos") or {})}
