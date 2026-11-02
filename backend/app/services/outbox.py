"""Outbound communications with delivery guarantees: every email goes through the outbox, failed
sends are retried with exponential backoff, and after the last attempt the message is dead-lettered,
an issue is raised on the case, and the owner is told in-app (the fallback channel)."""

from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.clock import now
from app.integrations import mail
from app.models import Case, Communication, Notification, User
from app.services import audit, ledger

MAX_ATTEMPTS = 3
BACKOFF_S = [30, 90, 270]


def notify(db: Session, user: User | None, text: str, *, kind: str, case: Case | None = None) -> None:
    if user is None:
        return
    db.add(Notification(user_id=user.id, case_id=case.id if case else None, kind=kind, text=text))
    db.flush()


def notify_role(db: Session, roles: set[str], region: str | None, text: str, *, kind: str, case: Case | None = None) -> None:
    for u in db.scalars(select(User).where(User.role.in_(roles), User.active.is_(True))):
        if region is None or u.region in (region, "ALL"):
            notify(db, u, text, kind=kind, case=case)


def queue(db: Session, comm: Communication, actor: User | None) -> Communication:
    comm.status = "queued"
    comm.next_attempt_at = now()
    comm.sent_by_id = actor.id if actor else None
    db.flush()
    return attempt(db, comm)


def attempt(db: Session, comm: Communication) -> Communication:
    comm.attempts += 1
    case = db.get(Case, comm.case_id) if comm.case_id else None
    try:
        mail.send(db, comm.to, comm.subject, comm.body)
    except mail.DeliveryError as e:
        comm.last_error = str(e)
        if comm.attempts >= MAX_ATTEMPTS:
            comm.status = "dead"
            comm.next_attempt_at = None
            audit.record(db, action="comms.dead_letter", summary=f"Delivery failed {comm.attempts}× to {', '.join(comm.to)}: {e}. Moved to dead-letter.",
                         actor_type="system", actor_id="outbox", case_id=comm.case_id, entity="communication", entity_id=comm.id, pinned=True)
            if case:
                ledger.open_issue(db, case, "comms_failure", f"Email '{comm.subject[:60]}' could not be delivered",
                                  severity="high", detail={"communication_id": comm.id, "error": str(e)}, dedupe_key=f"comm-{comm.id}")
                owner = db.get(User, comm.sent_by_id) if comm.sent_by_id else (db.get(User, case.assignee_id) if case.assignee_id else None)
                notify(db, owner, f"[{case.ref}] Email to {', '.join(comm.to)} failed after {comm.attempts} attempts. "
                       "Resend from the Communication Center or contact the recipient directly.", kind="comms_failure", case=case)
        else:
            comm.status = "failed"
            comm.next_attempt_at = now() + timedelta(seconds=BACKOFF_S[comm.attempts - 1])
            audit.record(db, action="comms.retry_scheduled", summary=f"Delivery attempt {comm.attempts} failed ({e}); retrying at {comm.next_attempt_at:%H:%M:%S} UTC",
                         actor_type="system", actor_id="outbox", case_id=comm.case_id, entity="communication", entity_id=comm.id)
        db.flush()
        return comm
    comm.status = "sent"
    comm.sent_at = now()
    comm.last_error = None
    comm.next_attempt_at = None
    if case:
        for i in ledger.open_issues(case, "comms_failure"):
            if (i.detail or {}).get("communication_id") == comm.id:
                i.status, i.resolution, i.resolved_at = "resolved", "delivered on retry", now()
    audit.record(db, action="comms.sent", summary=f"Email sent to {', '.join(comm.to)}: {comm.subject}",
                 actor_type="system", actor_id="outbox", case_id=comm.case_id, entity="communication", entity_id=comm.id,
                 meta={"attempt": comm.attempts})
    db.flush()
    return comm


def retry_due(db: Session) -> int:
    due = db.scalars(select(Communication).where(Communication.status == "failed", Communication.next_attempt_at <= now()))
    n = 0
    for comm in due:
        attempt(db, comm)
        n += 1
    return n


def system_email(db: Session, case: Case | None, to: list[str], subject: str, body: str, audience: str = "system") -> Communication:
    """Internal system notifications (approval requests, escalations) are sent without a human click;
    external stakeholder communications are never sent this way."""
    comm = Communication(case_id=case.id if case else None, audience=audience, to=to, subject=subject, body=body,
                         generated_by="system", status="draft")
    db.add(comm)
    db.flush()
    return queue(db, comm, None)
