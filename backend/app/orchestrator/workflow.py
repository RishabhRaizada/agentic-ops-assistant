"""Human-in-the-loop operations on a case: overrides, conflict resolution, approval routing and
decisions, escalation, cancellation and closure. Every function audits and enforces policy in code."""

import hashlib
import json
from datetime import timedelta
from typing import Any

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.clock import now
from app.models import ActionItem, Approval, Case, Communication, Recommendation, User
from app.orchestrator import pipeline
from app.orchestrator import states as S
from app.security import deny, ensure_can_write
from app.services import audit, comms_gen, ledger, outbox, policy, runtime_settings
from app.services.parsing import parse_int, parse_money_inr, parse_when

INT_KEYS = {"quantity_required", "quantity_available_claimed", "available_qty", "delay_days", "shortfall"}
DATE_KEYS = {"event_date", "decision_deadline"}
EDITABLE = INT_KEYS | DATE_KEYS | {"item", "location", "supplier", "event", "cost_mentioned", "customer", "store", "sku"}


def _human(db: Session, user: User, case: Case, action: str, summary: str, **kw) -> None:
    audit.record(db, action=action, summary=summary, actor_type="human", actor_id=user.email, case_id=case.id, **kw)


def _transition(db: Session, case: Case, to: str, user: User, reason: str) -> None:
    try:
        S.transition(db, case, to, actor_type="human", actor_id=user.email, reason=reason)
    except S.IllegalTransition as e:
        raise HTTPException(409, str(e)) from e


def _busy(case: Case) -> None:
    if case.processing:
        raise HTTPException(409, "The agents are still working on this request - try again in a few seconds")


# ------------------------------------------------------------------------------ corrections


def override_fact(db: Session, user: User, case: Case, key: str, raw: Any, reason: str) -> None:
    ensure_can_write(db, user, case, f"edit fact '{key}'")
    _busy(case)
    if key not in EDITABLE:
        raise HTTPException(400, f"'{key}' is computed by the system and cannot be edited directly")
    if not reason.strip():
        raise HTTPException(400, "A reason is required when overriding an AI value")
    val: Any = raw
    if key in INT_KEYS:
        val = parse_int(str(raw))
        if val is None:
            raise HTTPException(400, f"'{raw}' is not a number")
    elif key in DATE_KEYS:
        dt = parse_when(str(raw), now())
        if dt is None:
            raise HTTPException(400, f"Could not understand the date '{raw}'")
        val = dt.isoformat()
    elif key == "cost_mentioned":
        val = parse_money_inr(str(raw))
    fact = ledger.get(case, key)
    before = fact.value if fact else None
    if fact is None:
        fact = ledger.upsert(db, case, key, None, source="human", status="missing", confidence=0)
    fact.human_value, fact.edited_by_id, fact.edit_reason, fact.status = val, user.id, reason, "verified"
    fact.version += 1
    case.ledger_version += 1
    _human(db, user, case, "fact.override", f"Corrected '{fact.label}': {before!r} → {val!r} ({reason})",
           entity="fact", entity_id=fact.ref, before={"value": before, "ai_value": fact.ai_value}, after={"value": val}, pinned=True,
           meta={"ai_value_preserved": fact.ai_value})
    pipeline.supersede_approvals(db, case, reason=f"fact '{fact.label}' corrected by {user.name}")
    if case.status in (S.AWAITING_APPROVAL, S.IN_PROGRESS, S.APPROVED):
        _transition(db, case, S.ON_HOLD, user, "fact corrected; re-assessing")
    case.processing = True
    db.commit()
    pipeline.submit(pipeline.reanalyze, case.id, f"human correction of {fact.label}")


def resolve_issue(db: Session, user: User, case: Case, issue_id: int, resolution: str, choice: Any = None) -> None:
    ensure_can_write(db, user, case, "resolve an issue")
    _busy(case)
    issue = next((i for i in case.issues if i.id == issue_id), None)
    if not issue or issue.status != "open":
        raise HTTPException(404, "Issue not found or already resolved")
    rerun = False
    if issue.type == "conflict" and choice is not None:
        opt = next((o for o in issue.detail.get("options", []) if str(o["source"]) == str(choice)), None)
        if not opt:
            raise HTTPException(400, "Choose one of the offered sources")
        fact = ledger.get(case, issue.detail["fact_key"]) or ledger.upsert(db, case, issue.detail["fact_key"], None, source="human")
        before = fact.value
        fact.human_value, fact.edited_by_id, fact.edit_reason, fact.status = opt["value"], user.id, f"conflict resolved: {opt['label']}", "verified"
        case.ledger_version += 1
        resolution = resolution or opt["label"]
        _human(db, user, case, "conflict.resolved", f"Resolved conflict '{issue.title}': {opt['label']}", entity="fact",
               entity_id=fact.ref, before={"value": before}, after={"value": opt["value"], "source": opt["source"]}, pinned=True)
        rerun = True
    elif issue.type == "cancellation" and choice == "resume":
        rerun = True
    issue.status, issue.resolution, issue.resolved_by_id, issue.resolved_at = "resolved", resolution or "acknowledged", user.id, now()
    if issue.type in ("injection",):
        case.flags = {**case.flags, "injection_reviewed": True}
    _human(db, user, case, "issue.resolved", f"Resolved: {issue.title} - {issue.resolution}", entity="issue", entity_id=issue.id)
    if rerun:
        case.processing = True
        db.commit()
        pipeline.submit(pipeline.reanalyze, case.id, "issue resolved by human")
        return
    _maybe_ready(db, user, case)
    db.commit()


def _maybe_ready(db: Session, user: User, case: Case) -> None:
    """When the last blocking issue is cleared on a reviewed case, it becomes ready for approval."""
    if case.status == S.NEEDS_REVIEW and not [i for i in ledger.open_issues(case) if i.blocking]:
        has_plan = db.scalar(select(Recommendation).where(Recommendation.case_id == case.id, Recommendation.status == "proposed"))
        if has_plan:
            _transition(db, case, S.READY_FOR_APPROVAL, user, "blocking issues cleared")


# ------------------------------------------------------------------------------ recommendations & approvals


def add_custom_option(db: Session, user: User, case: Case, option: dict[str, Any]) -> Recommendation:
    ensure_can_write(db, user, case, "add an option")
    key = f"H{db.query(Recommendation).filter_by(case_id=case.id, source='human').count() + 1}"
    opt = {"key": key, "title": option["title"], "steps": option.get("steps") or [option["title"]],
           "est_cost_inr": float(option.get("est_cost_inr") or 0), "cost_breakdown": option.get("cost_breakdown") or "entered by operator",
           "covers_qty": int(option.get("covers_qty") or 0), "eta_days": float(option.get("eta_days") or 0),
           "meets_deadline": bool(option.get("meets_deadline", True)), "feasibility": option.get("feasibility", "medium"),
           "risks": option.get("risks") or [], "rationale": option.get("rationale") or "", "fact_refs": [], "tool_refs": [],
           "source": "human"}
    if not opt["rationale"].strip():
        raise HTTPException(400, "A justification is required for an operator-defined option")
    rec = Recommendation(case_id=case.id, ledger_version=case.ledger_version, option_key=key, option=opt, review={},
                         source="human", created_by_id=user.id,
                         plan_hash=hashlib.sha256(json.dumps([opt, case.ledger_version], sort_keys=True).encode()).hexdigest())
    db.add(rec)
    db.flush()
    _human(db, user, case, "option.custom", f"Added operator option {key}: {opt['title']} (₹{opt['est_cost_inr']:,.0f})",
           entity="recommendation", entity_id=rec.id, after=opt, pinned=True)
    return rec


def submit(db: Session, user: User, case: Case, rec_id: int, edits: dict[str, Any] | None, note: str) -> dict[str, Any]:
    ensure_can_write(db, user, case, "submit for approval")
    _busy(case)
    if case.status not in (S.READY_FOR_APPROVAL, S.NEEDS_REVIEW, S.REPLANNING):
        raise HTTPException(409, f"Cannot submit while the request is {case.status}")
    blocking = [i for i in ledger.open_issues(case) if i.blocking]
    if blocking:
        raise HTTPException(409, "Resolve blocking issues first: " + "; ".join(i.title for i in blocking))
    rec = db.get(Recommendation, rec_id)
    if not rec or rec.case_id != case.id or rec.status not in ("proposed", "selected"):
        raise HTTPException(404, "Option not found or no longer current")
    conf_block = runtime_settings.get(db, "conf_block")
    if rec.source == "ai" and (case.overall_confidence or 0) < conf_block and not note.strip():
        raise HTTPException(409, "AI confidence is too low to submit its recommendation without a written justification")
    if edits:
        before = dict(rec.option)
        rec.option = {**rec.option, **{k: v for k, v in edits.items() if k in ("est_cost_inr", "steps", "title", "covers_qty", "eta_days")},
                      "edited_by_human": True}
        rec.plan_hash = hashlib.sha256(json.dumps([rec.option, case.ledger_version], sort_keys=True, default=str).encode()).hexdigest()
        _human(db, user, case, "option.edited", f"Edited option {rec.option_key} before submitting", entity="recommendation",
               entity_id=rec.id, before=before, after=rec.option, pinned=True)
    for r in db.scalars(select(Recommendation).where(Recommendation.case_id == case.id, Recommendation.status == "selected")):
        r.status = "proposed"
    rec.status = "selected"
    if not rec.is_recommended and rec.source == "ai":
        _human(db, user, case, "recommendation.overridden", f"Chose option {rec.option_key} instead of the AI recommendation",
               entity="recommendation", entity_id=rec.id, meta={"note": note}, pinned=True)
    req = policy.requirement(db, case, rec.option)
    if not req["required"]:
        _transition(db, case, S.APPROVED, user, "no approval required by policy (no spend, low risk)")
        _human(db, user, case, "approval.not_required", "Policy: no approval required (no spend, low risk, high confidence)", pinned=True)
        rec.status = "approved"
        _start_execution(db, case, rec, user)
        db.commit()
        return {"approval_required": False}
    approval = _create_approval(db, case, rec, req["role"], req["reasons"], requested_by=user, level=0)
    _transition(db, case, S.AWAITING_APPROVAL, user, f"routed to {req['role'].replace('_', ' ')} ({', '.join(req['reasons'][:2])})")
    db.commit()
    return {"approval_required": True, "approval_id": approval.id, "role": req["role"], "reasons": req["reasons"]}


def _due_at(db: Session, case: Case) -> Any:
    hours = runtime_settings.get(db, "approval_sla_hours").get(case.priority or "P3", 24)
    due = now() + timedelta(hours=hours)
    if case.decision_deadline_at and case.decision_deadline_at > now() + timedelta(minutes=20):
        due = min(due, case.decision_deadline_at)
    return due


def _create_approval(db: Session, case: Case, rec: Recommendation, role: str, reasons: list[str], *,
                     requested_by: User | None, level: int) -> Approval:
    approver = policy.find_approver(db, role, case.region)
    a = Approval(case_id=case.id, recommendation_id=rec.id, plan_hash=rec.plan_hash, ledger_version=case.ledger_version,
                 amount_inr=float(rec.option.get("est_cost_inr") or 0), required_role=role, region=case.region or "ALL",
                 assigned_to_id=approver.id if approver else None, requested_by_id=requested_by.id if requested_by else None,
                 escalation_level=level, reasons=reasons, due_at=_due_at(db, case))
    db.add(a)
    db.flush()
    audit.record(db, action="approval.requested",
                 summary=f"Approval requested from {approver.name if approver else role} for option {rec.option_key} (₹{a.amount_inr:,.0f}), due {a.due_at:%H:%M} UTC",
                 actor_type="human" if requested_by else "system", actor_id=requested_by.email if requested_by else "sla-monitor",
                 case_id=case.id, entity="approval", entity_id=a.id, meta={"reasons": reasons, "role": role, "level": level}, pinned=True)
    if approver:
        outbox.notify(db, approver, f"[{case.ref}] Approval needed: {rec.option['title']} - ₹{a.amount_inr:,.0f}", kind="approval_request", case=case)
        outbox.system_email(db, case, [approver.email], f"[{case.ref}] Approval needed: {rec.option['title']}",
                            f"{approver.name},\n\nYour approval is requested for {case.ref} - {case.title}.\n"
                            f"Option {rec.option_key}: {rec.option['title']}\nAdditional cost: ₹{a.amount_inr:,.0f}\n"
                            f"Why approval is required: {'; '.join(reasons)}\nPlease decide in OpsPilot before {a.due_at:%d %b %H:%M} UTC.\n\nOpsPilot",
                            audience="approver_brief")
    return a


def decide(db: Session, user: User, approval_id: int, decision: str, reason: str, budget_cap: float | None,
           idempotency_key: str | None) -> dict[str, Any]:
    a = db.get(Approval, approval_id)
    if not a:
        raise HTTPException(404, "Approval not found")
    case = db.get(Case, a.case_id)
    if idempotency_key:
        prior = db.scalar(select(Approval).where(Approval.idempotency_key == idempotency_key))
        if prior:
            return {"status": prior.status, "idempotent_replay": True}
    ok, why = policy.can_decide(user, a, case)
    if not ok:
        raise deny(db, user, f"{decision} approval #{a.id} (₹{a.amount_inr:,.0f}, {a.required_role})", why, case, approval_id=a.id)
    rec = db.get(Recommendation, a.recommendation_id)
    if rec.plan_hash != a.plan_hash or a.ledger_version != case.ledger_version:
        a.status = "superseded"
        db.commit()
        raise HTTPException(409, "The plan or facts changed after this approval was requested; it has been withdrawn")
    if decision not in ("approve", "reject", "request_info"):
        raise HTTPException(400, "decision must be approve, reject or request_info")
    if decision in ("reject", "request_info") and not reason.strip():
        raise HTTPException(400, "A reason is required")
    a.decided_by_id, a.decided_at, a.decision_reason, a.idempotency_key = user.id, now(), reason, idempotency_key
    requester = db.get(User, a.requested_by_id) if a.requested_by_id else None
    if decision == "approve":
        a.status, rec.status = "approved", "approved"
        _transition(db, case, S.APPROVED, user, f"approved by {user.name}" + (f": {reason}" if reason else ""))
        _human(db, user, case, "approval.approved", f"Approved option {rec.option_key} (₹{a.amount_inr:,.0f})" + (f" - {reason}" if reason else ""),
               entity="approval", entity_id=a.id, pinned=True)
        outbox.notify(db, requester, f"[{case.ref}] Approved by {user.name}", kind="approval_decision", case=case)
        _start_execution(db, case, rec, user)
        db.commit()
        return {"status": "approved"}
    if decision == "request_info":
        a.status = "info_requested"
        _transition(db, case, S.NEEDS_REVIEW, user, f"approver asked for more information: {reason}")
        ledger.open_issue(db, case, "missing", f"Approver question: {reason}", severity="high", blocking=True,
                          detail={"from": user.name}, dedupe_key=f"approver-q-{a.id}")
        _human(db, user, case, "approval.info_requested", f"Requested more information: {reason}", entity="approval", entity_id=a.id, pinned=True)
        outbox.notify(db, requester, f"[{case.ref}] {user.name} needs more information: {reason}", kind="approval_decision", case=case)
        db.commit()
        return {"status": "info_requested"}
    # reject -> alternative action required
    a.status, rec.status, a.budget_cap_inr = "rejected", "rejected", budget_cap
    _human(db, user, case, "approval.rejected", f"Rejected option {rec.option_key}: {reason}" + (f" (budget cap ₹{budget_cap:,.0f})" if budget_cap else ""),
           entity="approval", entity_id=a.id, pinned=True)
    _transition(db, case, S.REPLANNING, user, f"rejected by {user.name}: {reason}")
    outbox.notify(db, requester, f"[{case.ref}] Option rejected by {user.name}: {reason}. Re-planning alternatives.", kind="approval_decision", case=case)
    rejected = [r.option["title"] for r in db.scalars(select(Recommendation).where(Recommendation.case_id == case.id, Recommendation.status == "rejected"))]
    constraints = {"reason": reason, "rejected_titles": rejected, "budget_cap_inr": budget_cap,
                   "instruction": "The approver rejected the option(s) above. Propose alternatives that address the rejection reason."}
    case.processing = True
    db.commit()
    pipeline.submit(pipeline.replan, case.id, constraints)
    return {"status": "rejected", "replanning": True}


def _start_execution(db: Session, case: Case, rec: Recommendation, user: User | None) -> None:
    for step in rec.option.get("steps", []):
        db.add(ActionItem(case_id=case.id, title=step, owner=user.name if user else "", due_at=case.decision_deadline_at))
    S.transition(db, case, S.IN_PROGRESS, actor_type="system", actor_id="orchestrator", reason=f"executing option {rec.option_key}")
    for aud in ("supplier", "status_update"):
        comms_gen.generate(db, case, aud)
    db.flush()


# ------------------------------------------------------------------------------ escalation (SLA monitor)


def escalate_due(db: Session) -> int:
    """Approval delay: remind before the due time, escalate to the next level when it passes, and
    flag an SLA breach when the request's own deadline is missed."""
    n = 0
    remind_min = runtime_settings.get(db, "reminder_minutes_before_due")
    for a in db.scalars(select(Approval).where(Approval.status == "pending")):
        case = db.get(Case, a.case_id)
        approver = db.get(User, a.assigned_to_id) if a.assigned_to_id else None
        if a.reminded_at is None and now() >= a.due_at - timedelta(minutes=remind_min) and now() < a.due_at:
            a.reminded_at = now()
            outbox.notify(db, approver, f"[{case.ref}] Reminder: approval due at {a.due_at:%H:%M} UTC", kind="approval_reminder", case=case)
            audit.record(db, action="approval.reminder", summary=f"Reminder sent to {approver.name if approver else a.required_role} - due in ≤{remind_min} min",
                         actor_type="system", actor_id="sla-monitor", case_id=case.id, entity="approval", entity_id=a.id)
            n += 1
        if now() >= a.due_at:
            nxt = policy.next_role(a.required_role)
            rec = db.get(Recommendation, a.recommendation_id)
            if nxt:
                a.status = "escalated"
                audit.record(db, action="approval.escalated",
                             summary=f"No response from {approver.name if approver else a.required_role} by the due time - escalated to {nxt.replace('_', ' ')}",
                             actor_type="system", actor_id="sla-monitor", case_id=case.id, entity="approval", entity_id=a.id, pinned=True)
                case.flags = {**case.flags, "escalated": True}
                requester = db.get(User, a.requested_by_id) if a.requested_by_id else None
                new = _create_approval(db, case, rec, nxt, [*a.reasons, f"escalated: no response from {a.required_role} by due time"],
                                       requested_by=None, level=a.escalation_level + 1)
                new.requested_by_id = a.requested_by_id
                outbox.notify(db, requester, f"[{case.ref}] Approval escalated to {nxt.replace('_', ' ')}", kind="escalation", case=case)
                outbox.notify(db, approver, f"[{case.ref}] Approval escalated past you (no response by due time)", kind="escalation", case=case)
            elif not case.flags.get("sla_breached"):
                case.flags = {**case.flags, "sla_breached": True}
                ledger.open_issue(db, case, "sla_breach", "Top-level approval overdue - management alerted", severity="high",
                                  detail={"approval_id": a.id})
                audit.record(db, action="sla.breach", summary="Approval overdue at the top of the escalation chain", actor_type="system",
                             actor_id="sla-monitor", case_id=case.id, pinned=True)
                outbox.notify_role(db, {"director", "regional_head"}, None, f"[{case.ref}] SLA breach: approval overdue", kind="sla_breach", case=case)
            n += 1
    # The request's own decision deadline (e.g. "by 4 PM today")
    for case in db.scalars(select(Case).where(Case.status.in_([S.AWAITING_APPROVAL, S.READY_FOR_APPROVAL, S.NEEDS_REVIEW, S.REPLANNING]),
                                              Case.decision_deadline_at.is_not(None))):
        if now() > case.decision_deadline_at and not (case.flags or {}).get("deadline_missed"):
            case.flags = {**case.flags, "deadline_missed": True, "sla_breached": True}
            ledger.open_issue(db, case, "sla_breach", f"Decision deadline passed ({case.decision_deadline_at:%d %b %H:%M} UTC) without a decision",
                              severity="high", dedupe_key="deadline")
            audit.record(db, action="sla.deadline_missed", summary="Requester's decision deadline passed without a decision",
                         actor_type="system", actor_id="sla-monitor", case_id=case.id, pinned=True)
            outbox.notify_role(db, {"ops_manager", "regional_head"}, case.region, f"[{case.ref}] Decision deadline missed", kind="sla_breach", case=case)
            n += 1
    db.flush()
    return n


# ------------------------------------------------------------------------------ execution, closure, cancellation


def complete_action(db: Session, user: User, case: Case, action_id: int) -> None:
    ensure_can_write(db, user, case, "complete an action")
    act = db.get(ActionItem, action_id)
    if not act or act.case_id != case.id:
        raise HTTPException(404, "Action not found")
    act.status, act.completed_by_id, act.completed_at = "done", user.id, now()
    _human(db, user, case, "action.completed", f"Completed: {act.title}", entity="action", entity_id=act.id)
    remaining = db.scalar(select(ActionItem).where(ActionItem.case_id == case.id, ActionItem.status == "open"))
    if not remaining and case.status == S.IN_PROGRESS:
        _transition(db, case, S.RESOLVED, user, "all actions completed")
    db.commit()


def close(db: Session, user: User, case: Case, reason: str, note: str) -> None:
    ensure_can_write(db, user, case, "close the request")
    if reason not in S.RESOLUTION_REASONS:
        raise HTTPException(400, f"resolution reason must be one of {S.RESOLUTION_REASONS}")
    if case.status not in (S.RESOLVED, S.NEEDS_INFO, S.NEEDS_REVIEW):
        raise HTTPException(409, f"Only resolved (or unactionable) requests can be closed; this one is {case.status}")
    case.resolution_reason, case.resolution_note = reason, note
    _transition(db, case, S.CLOSED, user, f"{reason}: {note}" if note else reason)
    db.commit()


def cancel(db: Session, user: User, case: Case, reason: str) -> None:
    ensure_can_write(db, user, case, "cancel the request")
    if not reason.strip():
        raise HTTPException(400, "A reason is required to cancel")
    n = 0
    for a in db.scalars(select(Approval).where(Approval.case_id == case.id, Approval.status == "pending")):
        a.status, a.decision_reason = "cancelled", f"request cancelled: {reason}"
        outbox.notify(db, db.get(User, a.assigned_to_id) if a.assigned_to_id else None,
                      f"[{case.ref}] Approval request cancelled: {reason}", kind="approval_cancelled", case=case)
        n += 1
    for act in db.scalars(select(ActionItem).where(ActionItem.case_id == case.id, ActionItem.status == "open")):
        act.status = "cancelled"
    for i in ledger.open_issues(case, "cancellation"):
        i.status, i.resolution, i.resolved_by_id, i.resolved_at = "resolved", "cancellation confirmed", user.id, now()
    case.resolution_reason = "cancelled_by_requester"
    case.resolution_note = reason
    _transition(db, case, S.CANCELLED, user, reason)
    _human(db, user, case, "case.cancelled", f"Cancelled: {reason}. {n} pending approval(s) withdrawn, open actions cancelled.", pinned=True)
    supplier_drafted = db.scalar(select(Communication).where(Communication.case_id == case.id, Communication.audience == "supplier"))
    comms_gen.generate(db, case, "status_update")
    if supplier_drafted:
        comms_gen.generate(db, case, "supplier")
    db.commit()


def merge_manual(db: Session, user: User, case: Case, target_ref: str) -> None:
    ensure_can_write(db, user, case, "merge requests")
    target = db.scalar(select(Case).where(Case.ref == target_ref))
    if not target or target.id == case.id or target.status in S.TERMINAL:
        raise HTTPException(404, "Target request not found or closed")
    pipeline.merge_into(db, target, case, "duplicate", f"confirmed duplicate by {user.name}", actor=("human", user.email))
    for i in ledger.open_issues(case, "duplicate"):
        i.status, i.resolution = "resolved", f"merged into {target_ref}"
    db.commit()


def split(db: Session, user: User, case: Case) -> list[str]:
    """Multi-issue request: create one linked child case per extra issue and process each."""
    ensure_can_write(db, user, case, "split the request")
    issues = (case.analysis.get("intake") or {}).get("issues", [])
    if len(issues) < 2:
        raise HTTPException(400, "Nothing to split")
    msg = case.messages[0]
    refs = []
    for idx, iss in enumerate(issues[1:], start=2):
        child = pipeline.ingest(db, channel=msg.channel, from_addr=msg.from_addr, from_name=msg.from_name,
                                subject=f"[Split {idx}/{len(issues)} of {case.ref}] {iss['title']}",
                                body=f"(Split from {case.ref}; this case covers only: {iss['title']} - {iss['requested_action']})\n\n{msg.body}",
                                user=user, received_at=msg.received_at, process=False)
        child.parent_case_id = case.id
        child.processing = True
        refs.append(child.ref)
        db.commit()
        pipeline.submit(pipeline.process_new, child.id)
    for i in ledger.open_issues(case, "ambiguity"):
        if i.detail.get("action") == "split":
            i.status, i.resolution, i.resolved_by_id = "resolved", f"split into {', '.join(refs)}", user.id
    _human(db, user, case, "case.split", f"Split into {', '.join(refs)}", pinned=True)
    _maybe_ready(db, user, case)
    db.commit()
    return refs


def send_comm(db: Session, user: User, comm: Communication, edits: dict[str, Any]) -> Communication:
    case = db.get(Case, comm.case_id) if comm.case_id else None
    if case:
        ensure_can_write(db, user, case, "send a communication")
    if comm.status not in ("draft", "dead", "failed"):
        raise HTTPException(409, f"Already {comm.status}")
    for k in ("subject", "body"):
        if edits.get(k):
            if edits[k] != getattr(comm, k):
                comm.generated_by = "human" if comm.generated_by != "ai" else "ai+human"
            setattr(comm, k, edits[k])
    if edits.get("to"):
        comm.to = edits["to"]
    if not comm.to:
        raise HTTPException(400, "Add at least one recipient")
    if comm.status in ("dead", "failed"):
        comm.attempts = 0
    audit.record(db, action="comms.approved_to_send", summary=f"{user.name} approved sending '{comm.subject}' to {', '.join(comm.to)}",
                 actor_type="human", actor_id=user.email, case_id=comm.case_id, entity="communication", entity_id=comm.id)
    outbox.queue(db, comm, user)
    db.commit()
    return comm
