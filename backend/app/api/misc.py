import csv
import io

from fastapi import APIRouter, Depends, Query
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.serialize import audit_out, iso
from app.db import get_db
from app.models import AuditEvent, Case, Notification, User
from app.security import current_user
from app.services import audit, reporting

router = APIRouter(prefix="/api", tags=["audit", "analytics"])


@router.get("/audit")
def list_audit(case_id: int | None = None, actor_type: str | None = None, action: str | None = None, q: str | None = None,
               limit: int = Query(300, le=2000), user: User = Depends(current_user), db: Session = Depends(get_db)):
    stmt = select(AuditEvent).order_by(AuditEvent.id.desc()).limit(limit)
    if case_id:
        stmt = stmt.where(AuditEvent.case_id == case_id)
    if actor_type:
        stmt = stmt.where(AuditEvent.actor_type == actor_type)
    if action:
        stmt = stmt.where(AuditEvent.action.like(f"{action}%"))
    if q:
        stmt = stmt.where(AuditEvent.summary.ilike(f"%{q}%"))
    refs = {c.id: c.ref for c in db.scalars(select(Case))}
    return [{**audit_out(e), "case_ref": refs.get(e.case_id)} for e in db.scalars(stmt)]


@router.get("/audit/verify")
def verify(user: User = Depends(current_user), db: Session = Depends(get_db)):
    return audit.verify(db)


@router.get("/audit/export.csv")
def export(user: User = Depends(current_user), db: Session = Depends(get_db)):
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["id", "ts", "case_id", "actor_type", "actor_id", "action", "summary", "prev_hash", "hash"])
    for e in db.scalars(select(AuditEvent).order_by(AuditEvent.id)):
        w.writerow([e.id, iso(e.ts), e.case_id, e.actor_type, e.actor_id, e.action, e.summary, e.prev_hash, e.hash])
    buf.seek(0)
    return StreamingResponse(iter([buf.getvalue()]), media_type="text/csv",
                             headers={"Content-Disposition": "attachment; filename=opspilot-audit.csv"})


@router.get("/analytics/summary")
def analytics(user: User = Depends(current_user), db: Session = Depends(get_db)):
    return reporting.metrics(db)


@router.post("/reports/management")
def management_report(user: User = Depends(current_user), db: Session = Depends(get_db)):
    rep = reporting.portfolio_digest(db)
    db.commit()
    return rep


@router.get("/notifications")
def notifications(user: User = Depends(current_user), db: Session = Depends(get_db)):
    rows = db.scalars(select(Notification).where(Notification.user_id == user.id).order_by(Notification.id.desc()).limit(50))
    refs = {c.id: c.ref for c in db.scalars(select(Case))}
    return [{"id": n.id, "kind": n.kind, "text": n.text, "read": n.read, "case_id": n.case_id, "case_ref": refs.get(n.case_id),
             "created_at": iso(n.created_at)} for n in rows]


@router.post("/notifications/read")
def mark_read(user: User = Depends(current_user), db: Session = Depends(get_db)):
    for n in db.scalars(select(Notification).where(Notification.user_id == user.id, Notification.read.is_(False))):
        n.read = True
    db.commit()
    return {"ok": True}
