"""Approval policy: when human approval is mandatory, who must approve, and whether a given user
may approve. This is deterministic code; no model output can change who approves or how much."""

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Approval, Case, User
from app.services import runtime_settings
from app.services.runtime_settings import ESCALATION_LADDER, ROLE_RANK


def role_for_amount(db: Session, amount: float) -> str:
    for band in runtime_settings.get(db, "approval_matrix"):
        if band["max_inr"] is None or amount <= band["max_inr"]:
            return band["role"]
    return "director"


def requirement(db: Session, case: Case, option: dict[str, Any]) -> dict[str, Any]:
    """Returns {required, reasons, role}. Approval is mandatory if ANY rule fires."""
    amount = float(option.get("est_cost_inr") or 0)
    conf_high = runtime_settings.get(db, "conf_high")
    reasons: list[str] = []
    if amount > 0:
        reasons.append(f"commits additional spend of ₹{amount:,.0f}")
    if case.risk_level in ("high", "critical"):
        reasons.append(f"risk level is {case.risk_level}")
    if case.category == "customer_commitment":
        reasons.append("changes a customer commitment")
    if any(k in " ".join(option.get("steps", [])).lower() for k in ("supplier", "purchase order", "po ")):
        reasons.append("places or changes a supplier order")
    if case.overall_confidence is not None and case.overall_confidence < conf_high:
        reasons.append(f"AI confidence {case.overall_confidence:.2f} is below {conf_high}")
    flags = case.flags or {}
    if flags.get("injection_suspected"):
        reasons.append("request contained suspected prompt-injection content")
    if flags.get("degraded_mode"):
        reasons.append("analysis ran in degraded mode")
    role = role_for_amount(db, amount)
    return {"required": bool(reasons), "reasons": reasons, "role": role}


def next_role(role: str) -> str | None:
    i = ESCALATION_LADDER.index(role) if role in ESCALATION_LADDER else -1
    return ESCALATION_LADDER[i + 1] if 0 <= i < len(ESCALATION_LADDER) - 1 else None


def find_approver(db: Session, role: str, region: str | None) -> User | None:
    users = db.scalars(select(User).where(User.role == role, User.active.is_(True))).all()
    for u in users:
        if u.region == region:
            return u
    return next((u for u in users if u.region == "ALL"), None)


def can_decide(user: User, approval: Approval, case: Case) -> tuple[bool, str]:
    """Every authorization rule for approving/rejecting, in the order a human would explain them."""
    if user.role not in ROLE_RANK or user.role == "analyst":
        return False, f"your role ({user.role}) cannot approve spend"
    if approval.status != "pending":
        return False, f"this approval is already {approval.status}"
    if ROLE_RANK[user.role] < ROLE_RANK.get(approval.required_role, 99):
        return False, f"requires {approval.required_role.replace('_', ' ')} or above"
    if user.region not in ("ALL", approval.region):
        return False, f"request is in region {approval.region}; your authority covers {user.region}"
    if approval.amount_inr > user.approval_limit_inr:
        return False, f"₹{approval.amount_inr:,.0f} exceeds your approval limit of ₹{user.approval_limit_inr:,.0f}"
    if approval.requested_by_id == user.id:
        return False, "you submitted this request; separation of duties forbids self-approval"
    return True, ""
