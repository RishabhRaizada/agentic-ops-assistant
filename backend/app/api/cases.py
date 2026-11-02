"""Request (case) endpoints: intake, detail, human corrections and lifecycle actions."""

import email
import email.policy
import email.utils
from typing import Any

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.serialize import _names, case_detail, case_summary, comm_out
from app.db import get_db
from app.models import Case, Communication, User
from app.orchestrator import pipeline, workflow
from app.orchestrator import states as S
from app.security import WRITE_ROLES, current_user, deny, ensure_can_write
from app.services import comms_gen, outbox

router = APIRouter(prefix="/api/requests", tags=["requests"])


class RequestIn(BaseModel):
    channel: str = "manual"
    from_addr: str = ""
    from_name: str = ""
    subject: str = Field(min_length=1, max_length=300)
    body: str = Field(min_length=5, max_length=20000)


def _case(db: Session, case_id: int) -> Case:
    c = db.get(Case, case_id)
    if not c:
        raise HTTPException(404, "Request not found")
    return c


def _can_create(db: Session, user: User) -> None:
    if user.role not in WRITE_ROLES:
        raise deny(db, user, "create a request", f"role '{user.role}' cannot create requests")


@router.get("")
def list_cases(
    status: str | None = None, risk: str | None = None, region: str | None = None, category: str | None = None,
    q: str | None = None, include_closed: bool = True, limit: int = Query(200, le=500),
    user: User = Depends(current_user), db: Session = Depends(get_db),
):
    stmt = select(Case).order_by(Case.id.desc()).limit(limit)
    if status:
        stmt = stmt.where(Case.status.in_(status.split(",")))
    if risk:
        stmt = stmt.where(Case.risk_level.in_(risk.split(",")))
    if region:
        stmt = stmt.where(Case.region == region)
    if category:
        stmt = stmt.where(Case.category == category)
    if not include_closed:
        stmt = stmt.where(Case.status.in_(S.OPEN))
    if q:
        like = f"%{q}%"
        stmt = stmt.where((Case.title.ilike(like)) | (Case.ref.ilike(like)) | (Case.summary.ilike(like)))
    users = _names(db)
    return [case_summary(c, users) for c in db.scalars(stmt)]


@router.post("", status_code=202)
def create(body: RequestIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    _can_create(db, user)
    case = pipeline.ingest(db, channel=body.channel, from_addr=body.from_addr or user.email, from_name=body.from_name or user.name,
                           subject=body.subject, body=body.body, user=user)
    return {"id": case.id, "ref": case.ref, "status": case.status}


@router.post("/upload", status_code=202)
async def upload_eml(file: UploadFile = File(...), user: User = Depends(current_user), db: Session = Depends(get_db)):
    _can_create(db, user)
    raw = await file.read()
    if len(raw) > 2_000_000:
        raise HTTPException(413, "File too large")
    msg = email.message_from_bytes(raw, policy=email.policy.default)
    part = msg.get_body(preferencelist=("plain", "html"))
    body = part.get_content() if part else ""
    if not body.strip():
        raise HTTPException(400, "No readable body in the .eml file")
    from_hdr = msg.get("From", "")
    name, addr = email.utils.parseaddr(from_hdr)
    case = pipeline.ingest(db, channel="eml", from_addr=addr, from_name=name, subject=msg.get("Subject", "(no subject)"),
                           body=body, user=user)
    return {"id": case.id, "ref": case.ref, "status": case.status}


@router.get("/{case_id}")
def detail(case_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    return case_detail(db, _case(db, case_id), user)


class FactIn(BaseModel):
    value: Any
    reason: str


@router.patch("/{case_id}/facts/{key}")
def override_fact(case_id: int, key: str, body: FactIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    workflow.override_fact(db, user, _case(db, case_id), key, body.value, body.reason)
    return {"ok": True, "reanalyzing": True}


class ResolveIn(BaseModel):
    resolution: str = ""
    choice: Any = None


@router.post("/{case_id}/issues/{issue_id}/resolve")
def resolve_issue(case_id: int, issue_id: int, body: ResolveIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    workflow.resolve_issue(db, user, _case(db, case_id), issue_id, body.resolution, body.choice)
    return {"ok": True}


@router.post("/{case_id}/reprocess")
def reprocess(case_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    case = _case(db, case_id)
    ensure_can_write(db, user, case, "re-run analysis")
    if case.processing:
        raise HTTPException(409, "Already processing")
    if case.status not in (S.NEEDS_INFO, S.NEEDS_REVIEW, S.READY_FOR_APPROVAL, S.ON_HOLD):
        raise HTTPException(409, f"Cannot re-run analysis while {case.status}")
    case.ledger_version += 1
    case.processing = True
    db.commit()
    pipeline.submit(pipeline.reanalyze, case.id, f"manual re-run by {user.name}")
    return {"ok": True}


class OptionIn(BaseModel):
    title: str
    steps: list[str] = []
    est_cost_inr: float = 0
    covers_qty: int = 0
    eta_days: float = 0
    rationale: str
    meets_deadline: bool = True


@router.post("/{case_id}/recommendations")
def custom_option(case_id: int, body: OptionIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    rec = workflow.add_custom_option(db, user, _case(db, case_id), body.model_dump())
    db.commit()
    return {"id": rec.id}


class SubmitIn(BaseModel):
    edits: dict[str, Any] | None = None
    note: str = ""


@router.post("/{case_id}/recommendations/{rec_id}/submit")
def submit(case_id: int, rec_id: int, body: SubmitIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    return workflow.submit(db, user, _case(db, case_id), rec_id, body.edits, body.note)


@router.post("/{case_id}/actions/{action_id}/complete")
def complete_action(case_id: int, action_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    workflow.complete_action(db, user, _case(db, case_id), action_id)
    return {"ok": True}


class CloseIn(BaseModel):
    reason: str
    note: str = ""


@router.post("/{case_id}/close")
def close(case_id: int, body: CloseIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    workflow.close(db, user, _case(db, case_id), body.reason, body.note)
    return {"ok": True}


class ReasonIn(BaseModel):
    reason: str


@router.post("/{case_id}/cancel")
def cancel(case_id: int, body: ReasonIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    workflow.cancel(db, user, _case(db, case_id), body.reason)
    return {"ok": True}


class MergeIn(BaseModel):
    target_ref: str


@router.post("/{case_id}/merge")
def merge(case_id: int, body: MergeIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    workflow.merge_manual(db, user, _case(db, case_id), body.target_ref)
    return {"ok": True}


@router.post("/{case_id}/split")
def split(case_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    return {"children": workflow.split(db, user, _case(db, case_id))}


class GenerateIn(BaseModel):
    audience: str


@router.post("/{case_id}/comms/generate")
def generate_comm(case_id: int, body: GenerateIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    case = _case(db, case_id)
    ensure_can_write(db, user, case, "draft a communication")
    if body.audience not in comms_gen.AUDIENCES:
        raise HTTPException(400, f"audience must be one of {list(comms_gen.AUDIENCES)}")
    comm = comms_gen.generate(db, case, body.audience, actor=user)
    db.commit()
    return comm_out(comm)


@router.post("/{case_id}/brief")
def case_brief(case_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    from app.services import reporting

    out = reporting.case_brief(db, _case(db, case_id))
    db.commit()
    return out


class CommEditIn(BaseModel):
    subject: str | None = None
    body: str | None = None
    to: list[str] | None = None


comm_router = APIRouter(prefix="/api/communications", tags=["communications"])


@comm_router.get("")
def list_comms(status: str | None = None, user: User = Depends(current_user), db: Session = Depends(get_db)):
    stmt = select(Communication).order_by(Communication.id.desc()).limit(300)
    if status:
        stmt = stmt.where(Communication.status.in_(status.split(",")))
    cases = {c.id: c for c in db.scalars(select(Case))}
    return [{**comm_out(c), "case_ref": cases[c.case_id].ref if c.case_id in cases else None} for c in db.scalars(stmt)]


@comm_router.patch("/{comm_id}")
def edit_comm(comm_id: int, body: CommEditIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    comm = db.get(Communication, comm_id)
    if not comm:
        raise HTTPException(404, "Not found")
    case = db.get(Case, comm.case_id) if comm.case_id else None
    if case:
        ensure_can_write(db, user, case, "edit a communication")
    if comm.status != "draft":
        raise HTTPException(409, "Only drafts can be edited")
    for k, v in body.model_dump(exclude_none=True).items():
        setattr(comm, k, v)
    comm.generated_by = "ai+human" if comm.generated_by == "ai" else comm.generated_by
    db.commit()
    return comm_out(comm)


@comm_router.post("/{comm_id}/send")
def send_comm(comm_id: int, body: CommEditIn | None = None, user: User = Depends(current_user), db: Session = Depends(get_db)):
    comm = db.get(Communication, comm_id)
    if not comm:
        raise HTTPException(404, "Not found")
    return comm_out(workflow.send_comm(db, user, comm, (body.model_dump(exclude_none=True) if body else {})))


@comm_router.post("/{comm_id}/fallback")
def fallback_in_app(comm_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    """Deliver a dead-lettered message through the in-app channel to internal recipients."""
    comm = db.get(Communication, comm_id)
    if not comm or comm.status != "dead":
        raise HTTPException(409, "Only dead-lettered messages can use the fallback channel")
    case = db.get(Case, comm.case_id) if comm.case_id else None
    internal = [u for u in db.scalars(select(User)) if u.email in comm.to]
    for u in internal:
        outbox.notify(db, u, f"{comm.subject}\n\n{comm.body[:500]}", kind="fallback_delivery", case=case)
    from app.services import audit

    audit.record(db, action="comms.fallback_channel", summary=f"Delivered via in-app fallback to {len(internal)} internal recipient(s); external recipients need a manual call",
                 actor_type="human", actor_id=user.email, case_id=comm.case_id, entity="communication", entity_id=comm.id)
    db.commit()
    return {"delivered_in_app": len(internal), "external_needing_manual_contact": [t for t in comm.to if t not in {u.email for u in internal}]}
