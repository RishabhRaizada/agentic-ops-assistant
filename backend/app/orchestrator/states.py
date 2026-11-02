"""Case lifecycle as an explicit state machine. Every status change goes through `transition`,
which rejects illegal moves and writes an audit event, so the timeline is the audit log."""

from sqlalchemy.orm import Session

from app.clock import now
from app.models import Case
from app.services import audit

NEW = "NEW"
SCREENING = "SCREENING"
ANALYZING = "ANALYZING"
NEEDS_INFO = "NEEDS_INFO"
NEEDS_REVIEW = "NEEDS_REVIEW"
READY_FOR_APPROVAL = "READY_FOR_APPROVAL"
AWAITING_APPROVAL = "AWAITING_APPROVAL"
REPLANNING = "REPLANNING"
APPROVED = "APPROVED"
IN_PROGRESS = "IN_PROGRESS"
RESOLVED = "RESOLVED"
CLOSED = "CLOSED"
ON_HOLD = "ON_HOLD"
MERGED = "MERGED"
CANCELLED = "CANCELLED"

TERMINAL = {CLOSED, MERGED, CANCELLED}
OPEN = {NEW, SCREENING, ANALYZING, NEEDS_INFO, NEEDS_REVIEW, READY_FOR_APPROVAL, AWAITING_APPROVAL,
        REPLANNING, APPROVED, IN_PROGRESS, RESOLVED, ON_HOLD}

TRANSITIONS: dict[str, set[str]] = {
    NEW: {SCREENING, MERGED, CANCELLED},
    SCREENING: {ANALYZING, NEEDS_REVIEW, MERGED},
    ANALYZING: {ANALYZING, NEEDS_INFO, NEEDS_REVIEW, READY_FOR_APPROVAL, MERGED, ON_HOLD},
    NEEDS_INFO: {ANALYZING, NEEDS_REVIEW, CANCELLED, CLOSED, MERGED, ON_HOLD},
    NEEDS_REVIEW: {ANALYZING, READY_FOR_APPROVAL, AWAITING_APPROVAL, APPROVED, NEEDS_INFO, CANCELLED, CLOSED, MERGED, ON_HOLD},
    READY_FOR_APPROVAL: {AWAITING_APPROVAL, APPROVED, ANALYZING, NEEDS_REVIEW, CANCELLED, ON_HOLD},
    AWAITING_APPROVAL: {APPROVED, REPLANNING, ON_HOLD, CANCELLED, NEEDS_INFO, NEEDS_REVIEW},
    REPLANNING: {READY_FOR_APPROVAL, NEEDS_REVIEW, AWAITING_APPROVAL, ON_HOLD, CANCELLED},
    ON_HOLD: {ANALYZING, CANCELLED, NEEDS_REVIEW},
    APPROVED: {IN_PROGRESS, ON_HOLD, CANCELLED},
    IN_PROGRESS: {RESOLVED, ON_HOLD, CANCELLED},
    RESOLVED: {CLOSED, IN_PROGRESS},
    CLOSED: set(),
    MERGED: set(),
    CANCELLED: set(),
}

STAMP = {
    NEEDS_INFO: None, READY_FOR_APPROVAL: "triaged_at", NEEDS_REVIEW: "triaged_at",
    AWAITING_APPROVAL: "submitted_at", APPROVED: "approved_at", RESOLVED: "resolved_at",
    CLOSED: "closed_at", CANCELLED: "cancelled_at",
}
PINNED = {AWAITING_APPROVAL, APPROVED, RESOLVED, CLOSED, CANCELLED, MERGED, ON_HOLD, NEEDS_INFO}

RESOLUTION_REASONS = ["resolved_as_planned", "resolved_alternative", "no_action_needed", "duplicate",
                      "cancelled_by_requester", "not_actionable", "insufficient_information"]


class IllegalTransition(Exception):
    pass


def transition(db: Session, case: Case, to: str, *, actor_type: str, actor_id: str, reason: str = "") -> None:
    frm = case.status
    if to not in TRANSITIONS.get(frm, set()):
        raise IllegalTransition(f"{case.ref}: cannot move from {frm} to {to}")
    if frm == to:
        return
    case.status = to
    stamp = STAMP.get(to)
    if stamp and getattr(case, stamp) is None:
        setattr(case, stamp, now())
    if to == NEEDS_INFO:
        case.sla_paused_at = case.sla_paused_at or now()  # waiting on requester pauses the SLA
    elif frm == NEEDS_INFO:
        case.sla_paused_at = None
    audit.record(
        db, action="status_change", summary=f"{frm} → {to}" + (f": {reason}" if reason else ""),
        actor_type=actor_type, actor_id=actor_id, case_id=case.id, before={"status": frm}, after={"status": to},
        pinned=to in PINNED,
    )
    db.flush()
