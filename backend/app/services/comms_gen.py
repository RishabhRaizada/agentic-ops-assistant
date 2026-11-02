"""Stakeholder communications. The Comms agent writes from verified facts only (grounded presenter
pattern); if it is unavailable a deterministic template is used. Drafts are never sent without a
human clicking Send."""

import json

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agents.base import load_prompt, run_agent
from app.agents.definitions import RefDeps, comms_agent
from app.integrations import systems
from app.models import Approval, Case, Communication, Recommendation, User
from app.services import ledger

AUDIENCES = {
    "requester_ack": "Acknowledge receipt to the requester: what we understood, current status, next step and when they will hear back.",
    "clarification": "Ask the requester for the missing or conflicting information listed in open_questions, explaining briefly why each is needed.",
    "supplier": "Write to the supplier about the delayed order: confirm the revised timeline, ask for any partial/expedited shipment options, and state what we need by when.",
    "store_manager": "Brief the store manager on the situation, the chosen plan and what it means for their launch/operations.",
    "status_update": "Update the requester on the decision and next steps.",
    "management": "Brief senior management: the issue, impact, decision taken or pending, and any asks.",
}


def recipients(db: Session, case: Case, audience: str) -> list[str]:
    original = next((m for m in case.messages if m.relation == "original"), case.messages[0] if case.messages else None)
    if audience in ("requester_ack", "clarification", "status_update"):
        return sorted({m.from_addr for m in case.messages if m.from_addr and m.relation in ("original", "duplicate")}) or (
            [original.from_addr] if original and original.from_addr else [])
    if audience == "supplier":
        sup = systems.resolve_supplier(db, ledger.value(case, "supplier"))
        return [sup.contact_email] if sup and sup.contact_email else []
    if audience == "store_manager":
        st = systems.store_by_code(db, case.store_code)
        return [st.manager_email] if st and st.manager_email else []
    if audience == "management":
        return [u.email for u in db.scalars(select(User).where(User.role == "director"))]
    return []


def _context(db: Session, case: Case) -> dict:
    rec = db.scalar(select(Recommendation).where(Recommendation.case_id == case.id, Recommendation.status.in_(["approved", "selected"]))
                    .order_by(Recommendation.id.desc()))
    pending = db.scalar(select(Approval).where(Approval.case_id == case.id, Approval.status == "pending"))
    return {
        "case_ref": case.ref,
        "title": case.title,
        "status": case.status,
        "risk_level": case.risk_level,
        "priority": case.priority,
        "chosen_plan": rec.option if rec and rec.status == "approved" else None,
        "plan_pending_approval": rec.option if rec and pending else None,
        "approval_status": "pending with " + pending.required_role if pending else None,
        "open_questions": (case.analysis.get("intake") or {}).get("clarifying_questions", []),
        "missing_info": [i.title for i in ledger.open_issues(case, "missing")],
        "conflicts": [i.title for i in ledger.open_issues(case, "conflict")],
    }


def _template(case: Case, audience: str, ctx: dict) -> tuple[str, str]:
    if audience == "clarification":
        qs = ctx["open_questions"] or ctx["missing_info"] or ["Please share the item, quantity, location and required date."]
        body = (f"Hello,\n\nThank you for raising this ({case.ref}). To assess it we need a few details:\n\n"
                + "\n".join(f"- {q}" for q in qs) + "\n\nWe will proceed as soon as we hear back.\n\nRegards,\nOperations Team")
        return f"[{case.ref}] Information needed: {case.title}", body
    status = case.status.replace("_", " ").lower()
    body = (f"Hello,\n\nThis is an update on {case.ref} - {case.title}.\nCurrent status: {status}."
            + (f"\nRisk level: {case.risk_level}." if case.risk_level else "")
            + "\n\nWe will keep you posted on next steps.\n\nRegards,\nOperations Team")
    return f"[{case.ref}] {case.title}", body


def generate(db: Session, case: Case, audience: str, *, actor: User | None = None) -> Communication:
    ctx = _context(db, case)
    facts = ledger.snapshot(case)
    prompt = load_prompt("comms")
    user_prompt = (
        f"<audience>{audience}: {AUDIENCES.get(audience, audience)}</audience>\n"
        f"<case>{json.dumps(ctx, default=str)}</case>\n<fact_ledger>{json.dumps(facts, default=str)}</fact_ledger>"
    )
    out = run_agent(db, agent=comms_agent, name="comms", step=f"draft:{audience}", prompt=prompt, user_prompt=user_prompt,
                    input_payload={"audience": audience, "ctx": ctx, "facts": facts}, case_id=case.id,
                    ledger_version=case.ledger_version, deps=RefDeps(fact_refs=ledger.refs(case)))
    if out.ok:
        subject, body, refs, by = out.output.subject, out.output.body, out.output.fact_refs, "ai"
        if not subject.startswith(f"[{case.ref}]"):
            subject = f"[{case.ref}] {subject}"
    else:
        (subject, body), refs, by = _template(case, audience, ctx), [], "template"
    comm = Communication(case_id=case.id, audience=audience, to=recipients(db, case, audience), subject=subject,
                         body=body, fact_refs=refs, generated_by=by, status="draft")
    db.add(comm)
    db.flush()
    return comm
