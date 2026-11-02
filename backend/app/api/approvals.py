from fastapi import APIRouter, Depends, Header
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.serialize import _names, approval_out
from app.db import get_db
from app.models import Approval, Case, Recommendation, User
from app.orchestrator import workflow
from app.security import current_user

router = APIRouter(prefix="/api/approvals", tags=["approvals"])


@router.get("")
def list_approvals(scope: str = "mine", user: User = Depends(current_user), db: Session = Depends(get_db)):
    stmt = select(Approval).order_by(Approval.due_at)
    if scope == "mine":
        stmt = stmt.where(Approval.assigned_to_id == user.id, Approval.status == "pending")
    elif scope == "pending":
        stmt = stmt.where(Approval.status == "pending")
    elif scope == "history":
        stmt = stmt.where(Approval.status != "pending").order_by(Approval.id.desc())
    users = _names(db)
    out = []
    for a in db.scalars(stmt.limit(300)):
        out.append(approval_out(a, users, case=db.get(Case, a.case_id), rec=db.get(Recommendation, a.recommendation_id)))
    return out


class DecisionIn(BaseModel):
    decision: str
    reason: str = ""
    budget_cap_inr: float | None = None


@router.post("/{approval_id}/decision")
def decide(approval_id: int, body: DecisionIn, user: User = Depends(current_user), db: Session = Depends(get_db),
           idempotency_key: str | None = Header(default=None)):
    return workflow.decide(db, user, approval_id, body.decision, body.reason, body.budget_cap_inr, idempotency_key)
