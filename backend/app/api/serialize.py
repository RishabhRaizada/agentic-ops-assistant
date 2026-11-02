"""Response shaping. Kept separate from the ORM so the API contract is explicit."""

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.clock import now
from app.models import (
    ActionItem,
    AgentRun,
    Approval,
    AuditEvent,
    Case,
    Communication,
    Recommendation,
    User,
)
from app.services import policy


def iso(dt) -> str | None:
    return dt.isoformat() if dt else None


def user_out(u: User | None) -> dict[str, Any] | None:
    if not u:
        return None
    return {"id": u.id, "email": u.email, "name": u.name, "title": u.title, "role": u.role, "region": u.region,
            "approval_limit_inr": u.approval_limit_inr}


def _names(db: Session) -> dict[int, User]:
    return {u.id: u for u in db.scalars(select(User))}


def case_summary(c: Case, users: dict[int, User] | None = None) -> dict[str, Any]:
    return {
        "id": c.id, "ref": c.ref, "title": c.title, "summary": c.summary, "category": c.category, "status": c.status,
        "priority": c.priority, "risk_level": c.risk_level, "region": c.region, "store_code": c.store_code,
        "sku_code": c.sku_code, "overall_confidence": c.overall_confidence, "flags": c.flags or {},
        "processing": c.processing, "decision_deadline_at": iso(c.decision_deadline_at),
        "created_at": iso(c.created_at), "updated_at": iso(c.updated_at),
        "channel": c.messages[0].channel if c.messages else None,
        "from": (c.messages[0].from_name or c.messages[0].from_addr) if c.messages else None,
        "message_count": len(c.messages),
        "open_issue_count": len([i for i in c.issues if i.status == "open"]),
        "blocking_issue_count": len([i for i in c.issues if i.status == "open" and i.blocking]),
        "duplicate_of_id": c.duplicate_of_id,
        "parent_case_id": c.parent_case_id,
        "assignee": (users or {}).get(c.assignee_id).name if users and c.assignee_id in users else None,
    }


def approval_out(a: Approval, users: dict[int, User], case: Case | None = None, rec: Recommendation | None = None) -> dict[str, Any]:
    out = {
        "id": a.id, "case_id": a.case_id, "recommendation_id": a.recommendation_id, "amount_inr": a.amount_inr,
        "required_role": a.required_role, "region": a.region, "status": a.status, "escalation_level": a.escalation_level,
        "reasons": a.reasons, "due_at": iso(a.due_at), "reminded_at": iso(a.reminded_at), "created_at": iso(a.created_at),
        "decided_at": iso(a.decided_at), "decision_reason": a.decision_reason, "budget_cap_inr": a.budget_cap_inr,
        "assigned_to": user_out(users.get(a.assigned_to_id)), "requested_by": user_out(users.get(a.requested_by_id)),
        "decided_by": user_out(users.get(a.decided_by_id)),
        "overdue": a.status == "pending" and now() > a.due_at,
    }
    if case:
        out["case"] = case_summary(case, users)
    if rec:
        out["option"] = rec.option
        out["review"] = rec.review
        out["is_recommended"] = rec.is_recommended
    return out


def comm_out(c: Communication) -> dict[str, Any]:
    return {"id": c.id, "case_id": c.case_id, "audience": c.audience, "channel": c.channel, "to": c.to, "subject": c.subject,
            "body": c.body, "fact_refs": c.fact_refs, "generated_by": c.generated_by, "status": c.status, "attempts": c.attempts,
            "next_attempt_at": iso(c.next_attempt_at), "last_error": c.last_error, "sent_at": iso(c.sent_at),
            "created_at": iso(c.created_at)}


def audit_out(e: AuditEvent) -> dict[str, Any]:
    return {"id": e.id, "ts": iso(e.ts), "case_id": e.case_id, "actor_type": e.actor_type, "actor_id": e.actor_id,
            "action": e.action, "entity": e.entity, "entity_id": e.entity_id, "summary": e.summary, "before": e.before,
            "after": e.after, "meta": e.meta, "pinned": e.pinned, "hash": e.hash, "prev_hash": e.prev_hash}


def run_out(r: AgentRun) -> dict[str, Any]:
    return {"id": r.id, "agent": r.agent, "step": r.step, "prompt_version": r.prompt_version, "model": r.model,
            "fallback_used": r.fallback_used, "degraded": r.degraded, "ledger_version": r.ledger_version,
            "input_hash": r.input_hash, "input": r.input_json, "output": r.output_json, "status": r.status, "error": r.error,
            "validation_retries": r.validation_retries, "tool_calls": r.tool_calls, "confidence": r.confidence,
            "latency_ms": r.latency_ms, "tokens_in": r.tokens_in, "tokens_out": r.tokens_out, "started_at": iso(r.started_at)}


def case_detail(db: Session, c: Case, viewer: User) -> dict[str, Any]:
    users = _names(db)
    recs = db.scalars(select(Recommendation).where(Recommendation.case_id == c.id).order_by(Recommendation.id)).all()
    approvals = db.scalars(select(Approval).where(Approval.case_id == c.id).order_by(Approval.id)).all()
    rec_by_id = {r.id: r for r in recs}
    current = [r for r in recs if r.status in ("proposed", "selected", "approved")]
    policy_preview = {r.id: policy.requirement(db, c, r.option) for r in current}
    return {
        **case_summary(c, users),
        "intent_clarity": c.intent_clarity,
        "confidence_breakdown": c.confidence_breakdown or {},
        "analysis": c.analysis or {},
        "ledger_version": c.ledger_version,
        "resolution_reason": c.resolution_reason,
        "resolution_note": c.resolution_note,
        "timestamps": {k: iso(getattr(c, k)) for k in ("created_at", "triaged_at", "submitted_at", "approved_at", "resolved_at", "closed_at", "cancelled_at")},
        "sla_paused_at": iso(c.sla_paused_at),
        "messages": [{"id": m.id, "channel": m.channel, "from_addr": m.from_addr, "from_name": m.from_name, "subject": m.subject,
                      "body": m.body, "received_at": iso(m.received_at), "relation": m.relation, "guard_label": m.guard_label,
                      "guard_signals": m.guard_signals} for m in c.messages],
        "facts": [{"ref": f.ref, "key": f.key, "label": f.label, "source": f.source, "ai_value": f.ai_value, "human_value": f.human_value,
                   "value": f.value, "raw_text": f.raw_text, "status": f.status, "confidence": f.confidence,
                   "evidence_quote": f.evidence_quote, "grounded": f.grounded, "version": f.version,
                   "edited_by": users[f.edited_by_id].name if f.edited_by_id in users else None, "edit_reason": f.edit_reason}
                  for f in c.facts],
        "issues": [{"id": i.id, "type": i.type, "severity": i.severity, "title": i.title, "detail": i.detail, "status": i.status,
                    "blocking": i.blocking, "resolution": i.resolution, "created_at": iso(i.created_at),
                    "resolved_by": users[i.resolved_by_id].name if i.resolved_by_id in users else None} for i in c.issues],
        "recommendations": [{"id": r.id, "option_key": r.option_key, "option": r.option, "review": r.review,
                             "is_recommended": r.is_recommended, "source": r.source, "status": r.status, "ledger_version": r.ledger_version,
                             "policy": policy_preview.get(r.id), "created_at": iso(r.created_at)} for r in recs],
        "approvals": [approval_out(a, users, rec=rec_by_id.get(a.recommendation_id)) for a in approvals],
        "actions": [{"id": a.id, "title": a.title, "owner": a.owner, "status": a.status, "due_at": iso(a.due_at),
                     "completed_at": iso(a.completed_at)} for a in db.scalars(select(ActionItem).where(ActionItem.case_id == c.id).order_by(ActionItem.id))],
        "communications": [comm_out(x) for x in db.scalars(select(Communication).where(Communication.case_id == c.id).order_by(Communication.id))],
        "agent_runs": [run_out(r) for r in db.scalars(select(AgentRun).where(AgentRun.case_id == c.id).order_by(AgentRun.id))],
        "timeline": [audit_out(e) for e in db.scalars(select(AuditEvent).where(AuditEvent.case_id == c.id).order_by(AuditEvent.id))],
        "children": [case_summary(x, users) for x in db.scalars(select(Case).where(Case.parent_case_id == c.id))],
        "merged": [case_summary(x, users) for x in db.scalars(select(Case).where(Case.duplicate_of_id == c.id))],
        "duplicate_of_ref": db.get(Case, c.duplicate_of_id).ref if c.duplicate_of_id else None,
        "parent_ref": db.get(Case, c.parent_case_id).ref if c.parent_case_id else None,
        "can_write": _can_write(viewer, c),
    }


def _can_write(user: User, c: Case) -> bool:
    from app.security import can_write_case

    return can_write_case(user, c)[0]
