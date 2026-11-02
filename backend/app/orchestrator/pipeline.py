"""The orchestrator. Hub-and-spoke: agents never call each other; this module builds each agent's
context pack from the fact ledger, runs it, validates and stores the result, then decides the next
step in code. Steps:

  Guard → Intake → ledger + grounding → Correlation (dedup / update) → Reconcile vs systems
  → Risk agent ∥ risk rules → (NEEDS_INFO?) → Planner (tools) → Reviewer (≤1 revise loop)
  → confidence → status (NEEDS_REVIEW | READY_FOR_APPROVAL)

Pipelines run one at a time on a background worker (protects the LLM quota and SQLite)."""

import hashlib
import json
import logging
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from typing import Any

from rapidfuzz import fuzz
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agents.base import load_prompt, run_agent
from app.agents.contracts import IntakeResult, IssueExtraction, Plan
from app.agents.definitions import (
    IntakeDeps,
    PlannerDeps,
    RefDeps,
    correlate_agent,
    guard_agent,
    intake_agent,
    planner_agent,
    reviewer_agent,
    risk_agent,
)
from app.clock import local_now, now
from app.db import session_scope
from app.integrations import systems
from app.models import Approval, Case, CaseMessage, Recommendation, User
from app.orchestrator import states as S
from app.services import (
    audit,
    comms_gen,
    confidence,
    degraded,
    injection,
    ledger,
    outbox,
    risk_rules,
    runtime_settings,
)
from app.services.parsing import is_substring, normalize_ws, parse_int, parse_money_inr, parse_when
from app.services.required_fields import HUMAN_NAMES, for_category

log = logging.getLogger("opspilot.pipeline")
_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="pipeline")
class _Sync:
    inline = False  # tests run jobs inline instead of on the worker thread


SYNC = _Sync()

INTAKE_FIELDS = {
    "item": "text", "quantity_required": "int", "quantity_available_claimed": "int", "location": "text",
    "supplier": "text", "delay_days": "int", "event": "text", "event_date": "date",
    "decision_deadline": "date", "cost_mentioned": "money", "customer": "text",
}


# =============================================================================== job plumbing


def submit(fn, *args) -> None:
    if getattr(SYNC, "inline", False):
        fn(*args)
        return
    _executor.submit(_guarded, fn, *args)


def _guarded(fn, *args) -> None:
    try:
        fn(*args)
    except Exception:  # the pipeline must never leave a case stuck in "processing"
        log.exception("pipeline job failed")
        case_id = args[0] if args else None
        if case_id:
            with session_scope() as db:
                case = db.get(Case, case_id)
                if case:
                    case.processing = False
                    ledger.open_issue(db, case, "degraded", "Automated analysis hit an internal error - please review manually",
                                      severity="high", blocking=True)
                    if S.NEEDS_REVIEW in S.TRANSITIONS.get(case.status, set()):
                        S.transition(db, case, S.NEEDS_REVIEW, actor_type="system", actor_id="orchestrator", reason="pipeline error")


def _mark(db: Session, case: Case, on: bool) -> None:
    case.processing = on
    db.commit()


def _sys(db: Session, case: Case, to: str, reason: str = "") -> None:
    S.transition(db, case, to, actor_type="system", actor_id="orchestrator", reason=reason)


def _flag(case: Case, name: str, value: Any = True) -> None:
    case.flags = {**(case.flags or {}), name: value}


def _note(case: Case, key: str, value: Any) -> None:
    case.analysis = {**(case.analysis or {}), key: value}


# =============================================================================== intake


def next_ref(db: Session) -> str:
    last = db.scalar(select(Case).order_by(Case.id.desc()).limit(1))
    return f"OPS-{(last.id if last else 0) + 1:04d}"


def ingest(db: Session, *, channel: str, from_addr: str, from_name: str, subject: str, body: str,
           user: User | None, received_at: datetime | None = None, process: bool = True) -> Case:
    case = Case(ref=next_ref(db), title=subject[:200] or "New request", created_by_id=user.id if user else None,
                assignee_id=user.id if user and user.role == "analyst" else None, flags={}, analysis={})
    db.add(case)
    db.flush()
    msg = CaseMessage(case_id=case.id, channel=channel, from_addr=from_addr, from_name=from_name, subject=subject,
                      body=body, received_at=received_at or now(),
                      content_hash=hashlib.sha256(normalize_ws(subject + "\n" + body).encode()).hexdigest())
    db.add(msg)
    case.messages.append(msg)
    actor_type, actor_id = ("human", user.email) if user else ("system", f"{channel}-intake")
    audit.record(db, action="request.received", summary=f"Request received via {channel} from {from_name or from_addr}: {subject}",
                 actor_type=actor_type, actor_id=actor_id, case_id=case.id, entity="message", entity_id=msg.id, pinned=True,
                 meta={"channel": channel, "content_hash": msg.content_hash})
    case.processing = process
    db.commit()
    if process:
        submit(process_new, case.id)
    return case


def _email_block(msg: CaseMessage) -> str:
    raw = f"From: {msg.from_name} <{msg.from_addr}>\nSubject: {msg.subject}\nReceived: {msg.received_at.astimezone(local_now().tzinfo):%A %d %b %Y %H:%M} IST\n\n{msg.body}"
    return f"<untrusted_email>\n{injection.datamark(raw)}\n</untrusted_email>"


def _screen(db: Session, case: Case, msg: CaseMessage) -> None:
    signals = injection.scan(f"{msg.subject}\n{msg.body}")
    label = injection.heuristic_label(signals)
    out = run_agent(db, agent=guard_agent, name="guard", step="screen", prompt=load_prompt("guard"),
                    user_prompt=_email_block(msg), input_payload={"message_id": msg.id, "hash": msg.content_hash}, case_id=case.id)
    if out.ok:
        label = injection.combine(label, out.output.label)
        signals += [{"signal": "guard_agent", "severity": "high" if out.output.label == "malicious" else "medium",
                     "span": s[:160], "source": "guard-agent"} for s in out.output.signals]
        if out.output.label == "safe" and label != "safe":
            signals.append({"signal": "guard_agent_disagrees", "severity": "low", "span": "guard agent judged the content safe", "source": "guard-agent"})
    msg.guard_label, msg.guard_signals = label, signals
    if label != "safe":
        _flag(case, "injection_suspected")
        ledger.open_issue(db, case, "injection", "Possible prompt injection / manipulation in the request" if label == "malicious"
                          else "Unusual instructions in the request", severity="high" if label == "malicious" else "medium",
                          blocking=label == "malicious", detail={"label": label, "signals": signals, "message_id": msg.id},
                          dedupe_key=f"msg-{msg.id}")
        audit.record(db, action="security.injection_flagged", summary=f"Message flagged '{label}' by guard ({len(signals)} signal(s)); content treated as data only and the case forced to human review",
                     actor_type="ai", actor_id="guard-agent", case_id=case.id, meta={"signals": signals}, pinned=True)


def _intake(db: Session, case: Case, msg: CaseMessage) -> tuple[IntakeResult, bool]:
    prompt = load_prompt("intake")
    out = run_agent(db, agent=intake_agent, name="intake", step="extract", prompt=prompt, user_prompt=_email_block(msg),
                    input_payload={"message_id": msg.id, "hash": msg.content_hash}, case_id=case.id,
                    deps=IntakeDeps(email_text=f"{msg.subject}\n{msg.body}"),
                    confidence_of=lambda o: sum(_field_scores(o.issues[0], f"{msg.subject}\n{msg.body}").values()) / len(INTAKE_FIELDS))
    if out.ok:
        return out.output, False
    result = degraded.extract(db, msg.subject, msg.body)
    audit.record(db, action="degraded.intake", summary="All LLMs unavailable: fields extracted by deterministic rules; case flagged for human review",
                 actor_type="system", actor_id="degraded-extractor", case_id=case.id, pinned=True)
    return result, True


def _field_scores(issue: IssueExtraction, text: str) -> dict[str, float]:
    scores = {}
    for name in INTAKE_FIELDS:
        f = getattr(issue, name)
        grounded = is_substring(f.evidence_quote, text) if f.value else None
        scores[name] = confidence.field_conf(f.confidence, f.status if f.value else "missing", grounded)
    return scores


GENERIC_VALUES = {
    "supplier": {"supplier", "our supplier", "the supplier", "vendor", "the vendor", "our vendor", "supplier name"},
    "item": {"items", "item", "stock", "goods", "products", "some items"},
    "location": {"store", "the store", "new store", "warehouse", "the warehouse"},
    "customer": {"customer", "the customer", "client"},
}


def _placeholder(name: str, value: str) -> bool:
    v = normalize_ws(value).strip(" .")
    if v in GENERIC_VALUES.get(name, set()):
        return True
    kind = INTAKE_FIELDS[name]
    return kind in ("int", "money") and not any(ch.isdigit() for ch in v) and parse_int(v) is None


def _write_intake_facts(db: Session, case: Case, issue: IssueExtraction, msg: CaseMessage, *, source: str = "email",
                        only: set[str] | None = None) -> None:
    text = f"{msg.subject}\n{msg.body}"
    for name, kind in INTAKE_FIELDS.items():
        if only is not None and name not in only:
            continue
        f = getattr(issue, name)
        if not f.value or f.status == "missing":
            if only is None:
                ledger.upsert(db, case, name, None, source=source, status="missing", confidence=0.0)
            continue
        if _placeholder(name, f.value):
            # The model echoed a generic word ("our supplier", "additional cost") - that is not a fact.
            if only is None:
                ledger.upsert(db, case, name, None, source=source, status="missing", confidence=0.0, raw_text=f.value)
            continue
        grounded = is_substring(f.evidence_quote, text)
        status = f.status
        val: Any = f.value
        if kind == "int":
            val = parse_int(f.value)
        elif kind == "money":
            val = parse_money_inr(f.value)
        elif kind == "date":
            dt = parse_when(f.value, msg.received_at)
            val = dt.isoformat() if dt else None
        if val is None:  # model gave text we cannot normalise: keep the words, lower trust
            val, status = f.value, "inferred"
        conf = confidence.field_conf(f.confidence, status, grounded)
        ledger.upsert(db, case, name, val, source=source, status=status, confidence=conf, raw_text=f.value,
                      evidence_quote=f.evidence_quote, grounded=grounded)
        if status == "stated" and not grounded:
            ledger.open_issue(db, case, "low_confidence", f"Unsupported extraction: '{ledger.LABELS.get(name, name)}' has no matching text in the email",
                              severity="medium", detail={"field": name, "value": f.value, "quote": f.evidence_quote}, dedupe_key=f"ungrounded-{name}")


# =============================================================================== correlation


def _candidates(db: Session, case: Case, issue: IssueExtraction) -> list[tuple[Case, float]]:
    store = systems.resolve_store(db, issue.location.value)
    sku = systems.resolve_sku(db, issue.item.value)
    q = select(Case).where(Case.id != case.id, Case.status.in_(S.OPEN), Case.created_at >= now() - timedelta(days=21))
    scored = []
    new_text = normalize_ws(f"{issue.title} {issue.item.value or ''} {issue.location.value or ''} {issue.supplier.value or ''}")
    for c in db.scalars(q):
        old_text = normalize_ws(f"{c.title} {c.summary}")
        s = fuzz.token_set_ratio(new_text, old_text) * 0.6
        if store and c.store_code == store.code:
            s += 25
        if sku and c.sku_code == sku.code:
            s += 20
        if s >= 55:
            scored.append((c, round(s, 1)))
    scored.sort(key=lambda x: -x[1])
    return scored[:3]


def _correlate(db: Session, case: Case, msg: CaseMessage, intake: IntakeResult) -> bool:
    """Returns True if the new case was folded into an existing one (pipeline stops)."""
    exact = db.scalar(select(CaseMessage).where(CaseMessage.content_hash == msg.content_hash, CaseMessage.id != msg.id))
    if exact:
        target = db.get(Case, exact.case_id)
        if target and target.status in S.OPEN:
            merge_into(db, target, case, "duplicate", "identical content already received", actor=("system", "dedup"))
            return True
    cands = _candidates(db, case, intake.issues[0])
    if not cands:
        return False
    payload = [{"ref": c.ref, "title": c.title, "summary": c.summary, "status": c.status, "match_score": s,
                "facts": ledger.snapshot(c, ["item", "quantity_required", "available_qty", "store", "supplier", "delay_days", "event_date", "decision_deadline"])}
               for c, s in cands]
    out = run_agent(db, agent=correlate_agent, name="correlation", step="dedup", prompt=load_prompt("correlate"),
                    user_prompt=f"<candidate_cases>{json.dumps(payload, default=str)}</candidate_cases>\n"
                                f"<new_message_extraction>{intake.model_dump_json()}</new_message_extraction>\n{_email_block(msg)}",
                    input_payload={"candidates": [p["ref"] for p in payload], "message": msg.content_hash}, case_id=case.id,
                    deps=RefDeps(fact_refs=set(), candidate_refs={p["ref"] for p in payload}))
    if not out.ok:
        top, score = cands[0]
        if score >= 85:
            ledger.open_issue(db, case, "duplicate", f"Possible duplicate of {top.ref} ({score:.0f}% match) - correlation agent unavailable, please confirm",
                              detail={"target_ref": top.ref, "score": score}, dedupe_key=f"dup-{top.ref}")
        return False
    c = out.output
    _note(case, "correlation", c.model_dump())
    target = next((x for x, _ in cands if x.ref == c.target_case_ref), None)
    auto = runtime_settings.get(db, "auto_link_duplicate_conf")
    if c.relation == "DUPLICATE" and target:
        if c.confidence >= auto:
            merge_into(db, target, case, "duplicate", c.rationale, actor=("ai", "correlation-agent"))
            return True
        ledger.open_issue(db, case, "duplicate", f"Possible duplicate of {target.ref} (confidence {c.confidence:.2f}) - confirm or keep separate",
                          detail={"target_ref": target.ref, "rationale": c.rationale}, dedupe_key=f"dup-{target.ref}")
    elif c.relation == "UPDATE" and target:
        if c.confidence >= 0.7:
            apply_update(db, target, case, msg, intake, c)
            return True
        ledger.open_issue(db, case, "material_change", f"May be an update to {target.ref} (confidence {c.confidence:.2f}) - confirm",
                          detail={"target_ref": target.ref, "changed_fields": c.changed_fields, "rationale": c.rationale}, dedupe_key=f"upd-{target.ref}")
    elif c.relation == "RELATED" and target:
        _note(case, "related_to", target.ref)
    return False


def merge_into(db: Session, target: Case, src: Case, relation: str, why: str, *, actor: tuple[str, str]) -> None:
    for m in list(src.messages):
        m.case_id, m.relation = target.id, relation
        target.messages.append(m)
    src.duplicate_of_id = target.id
    src.processing = False
    S.transition(db, src, S.MERGED, actor_type=actor[0], actor_id=actor[1], reason=f"{relation} of {target.ref}: {why}")
    src.resolution_reason = "duplicate"
    audit.record(db, action="case.linked", summary=f"{src.ref} linked to this case as {relation} ({why[:160]})",
                 actor_type=actor[0], actor_id=actor[1], case_id=target.id, meta={"source_case": src.ref}, pinned=True)
    owner = db.get(User, target.assignee_id) if target.assignee_id else None
    outbox.notify(db, owner, f"[{target.ref}] New {relation} message linked from {src.ref}", kind="duplicate", case=target)
    db.flush()


FIELD_ALIASES = {"quantity_available": "quantity_available_claimed", "cost": "cost_mentioned", "status": "event"}


def apply_update(db: Session, target: Case, src: Case, msg: CaseMessage, intake: IntakeResult, corr) -> None:
    """A later message changes the facts of a case in flight (e.g. supplier revises the date, or the
    launch is postponed). Facts are updated, approvals bound to the old facts are superseded, and the
    case is re-analysed - or, for a cancellation signal, held for a human to confirm."""
    merge_into(db, target, src, "update", corr.change_summary or corr.rationale, actor=("ai", "correlation-agent"))
    fields = {FIELD_ALIASES.get(f, f) for f in corr.changed_fields} & set(INTAKE_FIELDS)
    before = {k: ledger.value(target, k) for k in fields}
    _write_intake_facts(db, target, intake.issues[0], msg, source="update", only=fields)
    after = {k: ledger.value(target, k) for k in fields}
    target.ledger_version += 1
    audit.record(db, action="facts.updated_by_message", summary=f"Material change from new message: {corr.change_summary or ', '.join(fields)}",
                 actor_type="ai", actor_id="correlation-agent", case_id=target.id, before=before, after=after, pinned=True)
    superseded = supersede_approvals(db, target, reason=f"facts changed: {corr.change_summary or ', '.join(fields)}")
    if corr.cancellation_signal:
        ledger.open_issue(db, target, "cancellation", "Requester indicates the need is cancelled/postponed - confirm cancellation or resume",
                          severity="high", blocking=True, detail={"summary": corr.change_summary, "message_id": msg.id}, dedupe_key="cancel")
        if S.ON_HOLD in S.TRANSITIONS.get(target.status, set()):
            S.transition(db, target, S.ON_HOLD, actor_type="ai", actor_id="correlation-agent", reason="cancellation/postponement signal")
        outbox.notify_role(db, {"analyst", "ops_manager"}, target.region, f"[{target.ref}] Possible cancellation: {corr.change_summary}",
                           kind="cancellation", case=target)
        return
    ledger.open_issue(db, target, "material_change", f"Facts changed after workflow started: {corr.change_summary or ', '.join(fields)}",
                      severity="medium", detail={"before": before, "after": after, "approvals_superseded": superseded}, dedupe_key=f"chg-{msg.id}")
    if target.status in (S.AWAITING_APPROVAL, S.APPROVED, S.IN_PROGRESS, S.READY_FOR_APPROVAL, S.REPLANNING):
        S.transition(db, target, S.ON_HOLD, actor_type="ai", actor_id="correlation-agent", reason="material change - re-assessing")
    db.commit()
    reanalyze_inline(db, target, "material change from new message")


def supersede_approvals(db: Session, case: Case, *, reason: str) -> int:
    n = 0
    for a in db.scalars(select(Approval).where(Approval.case_id == case.id, Approval.status == "pending")):
        a.status, a.decision_reason = "superseded", reason
        approver = db.get(User, a.assigned_to_id) if a.assigned_to_id else None
        outbox.notify(db, approver, f"[{case.ref}] Approval request withdrawn: {reason}", kind="approval_superseded", case=case)
        audit.record(db, action="approval.superseded", summary=f"Pending approval withdrawn ({reason})", actor_type="system",
                     actor_id="orchestrator", case_id=case.id, entity="approval", entity_id=a.id, pinned=True)
        n += 1
    for r in db.scalars(select(Recommendation).where(Recommendation.case_id == case.id, Recommendation.status.in_(["proposed", "selected"]))):
        r.status = "superseded"
    db.flush()
    return n


# =============================================================================== reconcile vs systems


def _reconcile(db: Session, case: Case) -> None:
    v = lambda k: ledger.value(case, k)  # noqa: E731
    store_fact = ledger.get(case, "store")
    store = systems.store_by_code(db, store_fact.human_value) if store_fact and store_fact.human_value else systems.resolve_store(db, v("location"))
    if store:
        ledger.upsert(db, case, "store", store.code, source="derived", status="verified", raw_text=store.name)
        case.store_code, case.region = store.code, store.region
        ledger.resolve_key(db, case, "missing", "unknown-location", "store resolved")
    elif v("location"):
        ledger.open_issue(db, case, "missing", f"Location '{v('location')}' does not match a known store", severity="medium",
                          detail={"field": "location"}, dedupe_key="unknown-location")
    sku_fact = ledger.get(case, "sku")
    sku = None
    if sku_fact and sku_fact.human_value:
        sku = next(iter([s for s in [systems.resolve_sku(db, sku_fact.human_value)] if s]), None)
    sku = sku or systems.resolve_sku(db, v("item"))
    if sku:
        ledger.upsert(db, case, "sku", sku.code, source="derived", status="verified", raw_text=sku.name)
        case.sku_code = sku.code
        ledger.resolve_key(db, case, "missing", "unknown-sku", "SKU resolved")
    elif v("item"):
        ledger.open_issue(db, case, "missing", f"Item '{v('item')}' does not match a known SKU", severity="medium",
                          detail={"field": "item"}, dedupe_key="unknown-sku")

    # --- inventory: the system of record vs what the email claims
    system_avail = None
    if sku and store:
        loc = store.serving_dc or store.code
        try:
            rows = systems.get_inventory(db, sku.code, loc)
            system_avail = sum(r["available"] for r in rows)
            raw = "; ".join(f"{r['location']}: on hand {r['on_hand']}, reserved {r['reserved']}" for r in rows) or f"no stock record at {loc}"
            ledger.upsert(db, case, "inventory_available", system_avail, source="inventory", status="verified", raw_text=raw)
            ledger.resolve_key(db, case, "system_unavailable", "inventory", "inventory system reachable again")
        except systems.SystemUnavailable as e:
            ledger.upsert(db, case, "inventory_available", None, source="inventory", status="unverified", confidence=0.0, raw_text=str(e))
            ledger.open_issue(db, case, "system_unavailable", "Inventory system unavailable - stock figures from the email are unverified",
                              severity="medium", detail={"error": str(e), "retry": "re-run analysis when the system recovers"}, dedupe_key="inventory")
            audit.record(db, action="integration.unavailable", summary=f"{e} - continuing with unverified email figures (retry available)",
                         actor_type="system", actor_id="reconciler", case_id=case.id, pinned=True)
    claimed = v("quantity_available_claimed")
    avail_fact = ledger.get(case, "available_qty")
    human_choice = avail_fact.human_value if avail_fact else None
    if isinstance(claimed, int) and system_avail is not None and claimed != system_avail and human_choice is None:
        ledger.open_issue(
            db, case, "conflict", f"Available units: request says {claimed}, inventory system shows {system_avail}",
            severity="high", blocking=True, dedupe_key="available_qty",
            detail={"fact_key": "available_qty", "options": [
                {"source": "inventory", "label": f"Use inventory system ({system_avail})", "value": system_avail},
                {"source": "email", "label": f"Use request figure ({claimed})", "value": claimed}]})
    elif human_choice is None:
        ledger.resolve_key(db, case, "conflict", "available_qty", "values now agree")
    effective = system_avail if system_avail is not None else claimed if isinstance(claimed, int) else None
    ledger.upsert(db, case, "available_qty", effective, source="inventory" if system_avail is not None else "email",
                  status="verified" if system_avail is not None else "unverified")
    req = v("quantity_required")
    avail = v("available_qty")
    if isinstance(req, int) and isinstance(avail, int):
        ledger.upsert(db, case, "shortfall", max(0, req - avail), source="derived", status="verified")

    # --- calendar & PO: when is the stock needed and when will it arrive
    today = local_now()
    event_dt = None
    ev = v("event_date")
    if isinstance(ev, str) and ev[:4].isdigit():
        event_dt = datetime.fromisoformat(ev)
    event_text = (v("event") or "").lower()
    if store and store.launch_date:
        cal = store.launch_date.astimezone(today.tzinfo)
        ledger.upsert(db, case, "launch_date", cal.date().isoformat(), source="calendar", status="verified",
                      raw_text=f"{store.name}: {store.status}")
        human_event = (ledger.get(case, "event_date") or None) and ledger.get(case, "event_date").human_value is not None
        if ("launch" in event_text or "open" in event_text or event_dt is None) and not human_event:
            if event_dt is not None and abs((event_dt.date() - cal.date()).days) > 3:
                ledger.open_issue(db, case, "conflict", f"Event date in request ({event_dt:%d %b}) differs from store calendar ({cal:%d %b})",
                                  severity="medium", dedupe_key="event_date",
                                  detail={"fact_key": "event_date", "options": [
                                      {"source": "calendar", "label": f"Use store calendar ({cal:%d %b})", "value": cal.isoformat()},
                                      {"source": "email", "label": f"Use request ({event_dt:%d %b})", "value": event_dt.isoformat()}]})
            event_dt = cal
    if event_dt:
        ledger.upsert(db, case, "days_to_event", (event_dt.date() - today.date()).days, source="derived", status="verified")
    if sku:
        try:
            pos = [p for p in systems.get_purchase_orders(db, sku.code) if p["status"] in ("open", "delayed")]
        except systems.SystemUnavailable:
            pos = []
        po = next((p for p in pos if store and p["dest_location"] in (store.code, store.serving_dc)), pos[0] if pos else None)
        delay = v("delay_days")
        if po:
            ledger.upsert(db, case, "po", f"{po['po_number']}: {po['quantity']} units from {po['supplier_code']}, promised {po['promised_date']}",
                          source="po", status="verified")
            sup_fact = ledger.get(case, "supplier")
            if sup_fact is not None and sup_fact.value is None:
                sup = systems.resolve_supplier(db, po["supplier_code"]) or next(
                    (s for s in db.scalars(select(systems.Supplier)) if s.code == po["supplier_code"]), None)
                if sup:
                    # Missing in the email but known to the PO system: fill it, marked as inferred.
                    ledger.upsert(db, case, "supplier", sup.name, source="po", status="inferred", confidence=0.8,
                                  raw_text=f"from {po['po_number']}")
                    ledger.resolve_key(db, case, "missing", "missing-supplier", f"inferred from {po['po_number']}")
            base = datetime.fromisoformat(po["promised_date"]).replace(tzinfo=today.tzinfo)
            arrival = base + timedelta(days=delay) if isinstance(delay, int) else (
                datetime.fromisoformat(po["revised_date"]).replace(tzinfo=today.tzinfo) if po["revised_date"] else base)
        elif isinstance(delay, int):
            first = min(m.received_at for m in case.messages)
            arrival = first.astimezone(today.tzinfo) + timedelta(days=delay)
        else:
            arrival = None
        if arrival:
            ledger.upsert(db, case, "expected_arrival", arrival.date().isoformat(), source="derived", status="verified")
            if event_dt:
                slip = max(0, (arrival.date() - event_dt.date()).days)
                ledger.upsert(db, case, "slip_days", slip, source="derived", status="verified")
                if store and store.expected_daily_revenue_inr and slip:
                    ledger.upsert(db, case, "daily_revenue", store.expected_daily_revenue_inr, source="calendar", status="verified")
                    ledger.upsert(db, case, "revenue_at_risk", store.expected_daily_revenue_inr * slip, source="derived", status="verified")
    dl = v("decision_deadline")
    if isinstance(dl, str) and dl[:4].isdigit():
        d = datetime.fromisoformat(dl)
        case.decision_deadline_at = d
        ledger.upsert(db, case, "hours_to_deadline", round((d - now()).total_seconds() / 3600, 1), source="derived", status="verified")


def _missing(db: Session, case: Case) -> list[str]:
    critical, important = for_category(case.category)
    critical_missing = []
    keep = set()
    for k in critical + important:
        val = ledger.value(case, k)
        if val is None or val == "":
            key = f"missing-{k}"
            keep.add(key)
            ledger.open_issue(db, case, "missing", f"Missing: {HUMAN_NAMES.get(k, k)}", severity="high" if k in critical else "low",
                              blocking=k in critical, detail={"field": k}, dedupe_key=key)
            if k in critical:
                critical_missing.append(k)
    for i in ledger.open_issues(case, "missing"):
        key = (i.detail or {}).get("_key", "")
        if key.startswith("missing-") and key not in keep:
            i.status, i.resolution, i.resolved_at = "resolved", "information now available", now()
    return critical_missing


# =============================================================================== risk


def _assess_risk(db: Session, case: Case) -> dict[str, Any]:
    vals = {f.key: f.value for f in case.facts}
    rules = risk_rules.score(vals, case.category)
    facts = ledger.snapshot(case)
    out = run_agent(db, agent=risk_agent, name="risk", step="assess", prompt=load_prompt("risk"),
                    user_prompt=f"<case category='{case.category}'>{case.title}\n{case.summary}</case>\n<fact_ledger>{json.dumps(facts, default=str)}</fact_ledger>\n"
                                f"<today>{local_now():%A %d %b %Y %H:%M} IST</today>",
                    input_payload={"facts": facts, "category": case.category}, case_id=case.id, ledger_version=case.ledger_version,
                    deps=RefDeps(fact_refs=ledger.refs(case)))
    agent_level = out.output.risk_level if out.ok else None
    rec = risk_rules.reconcile(agent_level, rules["level"])
    case.risk_level = rec["final"]
    case.priority = min(out.output.urgency, rules["urgency"]) if out.ok else rules["urgency"]
    risk = {"agent": out.output.model_dump() if out.ok else None, "rules": rules, "reconciled": rec}
    _note(case, "risk", risk)
    if rec["contradiction"]:
        ledger.open_issue(db, case, "contradiction", f"Risk agent says '{agent_level}' but rules engine says '{rules['level']}' - human judgement needed",
                          severity="high", blocking=True, detail={"agent": agent_level, "rules": rules["level"], "rules_factors": rules["factors"]},
                          dedupe_key="risk")
        audit.record(db, action="agents.contradiction", summary=f"Contradictory risk assessments (agent={agent_level}, rules={rules['level']}); using the higher and requiring human acknowledgement",
                     actor_type="system", actor_id="risk-reconciler", case_id=case.id, pinned=True)
    else:
        ledger.auto_resolve(db, case, "contradiction", set(), "assessments agree after re-analysis")
    return risk


# =============================================================================== plan + review


def _plan_prompt(case: Case, deps: PlannerDeps, constraints: dict[str, Any], feedback: str | None) -> str:
    facts = ledger.snapshot(case)
    task = {
        "store_code": case.store_code, "sku_code": case.sku_code, "serving_dc": deps.dc_code,
        "shortfall_units": ledger.value(case, "shortfall"), "event_date": ledger.value(case, "launch_date") or ledger.value(case, "event_date"),
        "days_to_event": ledger.value(case, "days_to_event"), "risk_level": case.risk_level,
    }
    parts = [f"<case category='{case.category}'>{case.title}\n{case.summary}</case>",
             f"<fact_ledger>{json.dumps(facts, default=str)}</fact_ledger>",
             f"<task>{json.dumps(task, default=str)}</task>",
             f"<constraints>{json.dumps(constraints or {}, default=str)}</constraints>"]
    if feedback:
        parts.append(f"<reviewer_feedback>{feedback}</reviewer_feedback>\nRevise the plan to fix every point above.")
    return "\n".join(parts)


def _plan(db: Session, case: Case, constraints: dict[str, Any] | None = None) -> dict[str, Any]:
    constraints = constraints or {}
    store = systems.store_by_code(db, case.store_code)

    def new_deps() -> PlannerDeps:
        return PlannerDeps(db=db, fact_refs=ledger.refs(case), sku_code=case.sku_code, store_code=case.store_code,
                           dc_code=store.serving_dc if store else None, budget_cap_inr=constraints.get("budget_cap_inr"),
                           rejected_titles=constraints.get("rejected_titles", []))

    deps = new_deps()
    out = run_agent(db, agent=planner_agent, name="planner", step="plan", prompt=load_prompt("planner"),
                    user_prompt=_plan_prompt(case, deps, constraints, None), input_payload={"ledger_version": case.ledger_version, "constraints": constraints},
                    case_id=case.id, ledger_version=case.ledger_version, deps=deps, thinking=True, request_limit=16)
    revised = False
    review = None
    if out.ok:
        plan: Plan = out.output
        review = _review(db, case, plan, deps, constraints)
        if review and review.verdict == "revise":
            feedback = "\n".join(f"- option {i.option_key}: {i.problem}" for i in review.issues) or review.summary
            deps2 = new_deps()
            out2 = run_agent(db, agent=planner_agent, name="planner", step="revise", prompt=load_prompt("planner"),
                             user_prompt=_plan_prompt(case, deps2, constraints, feedback),
                             input_payload={"ledger_version": case.ledger_version, "feedback": feedback}, case_id=case.id,
                             ledger_version=case.ledger_version, deps=deps2, thinking=True, request_limit=16)
            if out2.ok:
                plan, deps, revised = out2.output, deps2, True
                review = _review(db, case, plan, deps, constraints, step="re-review") or review
        source = "ai"
    else:
        plan = degraded.plan(db, case, deps, constraints)
        source = "rules"
        audit.record(db, action="degraded.plan", summary="Planner unavailable: options computed by deterministic rules from system data",
                     actor_type="system", actor_id="degraded-planner", case_id=case.id, pinned=True)
    # Supersede earlier proposals and store the new ones.
    for r in db.scalars(select(Recommendation).where(Recommendation.case_id == case.id, Recommendation.status.in_(["proposed", "selected"]))):
        r.status = "superseded"
    review_by_key: dict[str, list] = {}
    if review:
        for i in review.issues:
            review_by_key.setdefault(i.option_key, []).append(i.model_dump())
    for o in plan.options:
        opt = o.model_dump()
        opt["tool_evidence"] = {r: deps.tool_results[r] for r in o.tool_refs if r in deps.tool_results}
        opt["source"] = source
        db.add(Recommendation(case_id=case.id, ledger_version=case.ledger_version, option_key=o.key, option=opt,
                              review={"verdict": review.verdict if review else None, "issues": review_by_key.get(o.key, [])},
                              is_recommended=o.key == plan.recommended_key, source="ai",
                              plan_hash=hashlib.sha256(json.dumps([opt, case.ledger_version], sort_keys=True, default=str).encode()).hexdigest()))
    _note(case, "plan", {"recommended_key": plan.recommended_key, "rationale": plan.recommendation_rationale,
                         "open_questions": plan.open_questions, "confidence": plan.confidence, "source": source,
                         "review": review.model_dump() if review else None, "revised": revised,
                         "tools_used": [{"ref": r, "tool": e["tool"], "error": e.get("error")} for r, e in deps.tool_results.items()]})
    db.flush()
    return {"plan": plan, "review": review, "revised": revised, "source": source,
            "citations_valid": all(set(o.tool_refs) <= deps.ok_refs for o in plan.options),
            "tool_failures": [e for e in deps.tool_results.values() if "error" in e]}


def _review(db: Session, case: Case, plan: Plan, deps: PlannerDeps, constraints: dict, step: str = "review"):
    payload = {"plan": plan.model_dump(), "tool_results": deps.tool_results, "facts": ledger.snapshot(case), "constraints": constraints}
    out = run_agent(db, agent=reviewer_agent, name="reviewer", step=step, prompt=load_prompt("reviewer"),
                    user_prompt=json.dumps(payload, default=str), input_payload={"plan_keys": [o.key for o in plan.options]},
                    case_id=case.id, ledger_version=case.ledger_version, thinking="low")
    return out.output if out.ok else None


# =============================================================================== the main flows


def process_new(case_id: int) -> None:
    with session_scope() as db:
        case = db.get(Case, case_id)
        msg = case.messages[0]
        _sys(db, case, S.SCREENING, "screening content")
        _screen(db, case, msg)
        db.commit()
        _sys(db, case, S.ANALYZING, "extracting")
        intake, was_degraded = _intake(db, case, msg)
        if was_degraded:
            _flag(case, "degraded_mode")
        _note(case, "intake", intake.model_dump())
        issue = intake.issues[0]
        case.title, case.summary, case.category, case.intent_clarity = issue.title[:200], intake.summary, issue.category, intake.intent_clarity
        if not intake.in_scope:
            ledger.open_issue(db, case, "out_of_scope", "Not an operations request - close or reclassify", severity="low", blocking=True)
            _sys(db, case, S.NEEDS_REVIEW, "out of scope")
            _mark(db, case, False)
            return
        if intake.intent_clarity == "multi_issue" and len(intake.issues) > 1:
            ledger.open_issue(db, case, "ambiguity", f"Request contains {len(intake.issues)} separate issues - split into linked cases",
                              severity="medium", detail={"issues": [{"index": i, "title": x.title, "category": x.category}
                                                                    for i, x in enumerate(intake.issues)], "action": "split"},
                              dedupe_key="multi")
        elif intake.intent_clarity == "ambiguous":
            ledger.open_issue(db, case, "ambiguity", "Expected action is unclear - confirm with the requester",
                              severity="medium", detail={"questions": intake.clarifying_questions}, dedupe_key="ambiguous")
        _write_intake_facts(db, case, issue, msg)
        db.commit()
        if msg.guard_label not in (None, "safe"):
            # Untrusted content may reference a real case; it must never merge into or edit it.
            cands = _candidates(db, case, issue)
            if cands:
                ledger.open_issue(db, case, "duplicate", f"Mentions the same issue as {cands[0][0].ref} - not linked automatically because the content is flagged",
                                  severity="medium", detail={"target_ref": cands[0][0].ref, "reason": "flagged content"}, dedupe_key=f"dup-{cands[0][0].ref}")
        elif not was_degraded and _correlate(db, case, msg, intake):
            _mark(db, case, False)
            return
        db.commit()
        analyze(db, case, first_run=True)
        _mark(db, case, False)


def analyze(db: Session, case: Case, *, first_run: bool = False, constraints: dict[str, Any] | None = None) -> None:
    """Everything after extraction. Re-entrant: human corrections and updates call it again with a
    bumped ledger version; earlier agent outputs remain in the trace."""
    _reconcile(db, case)
    critical_missing = _missing(db, case)
    db.commit()
    _assess_risk(db, case)
    db.commit()
    degraded_flag = bool((case.flags or {}).get("degraded_mode"))
    if critical_missing:
        conf = confidence.compute(field_confs=_ledger_confs(case), required=sum(for_category(case.category), []),
                                  agreement=case.analysis["risk"]["reconciled"]["agreement"],
                                  unresolved_conflicts=len(ledger.open_issues(case, "conflict")), review_verdict=None, revised=False,
                                  citations_valid=True, degraded=degraded_flag,
                                  system_unavailable=bool(ledger.open_issues(case, "system_unavailable")), planner_conf=None)
        case.overall_confidence, case.confidence_breakdown = conf["overall"], conf
        if not any(c.audience == "clarification" and c.status == "draft" for c in _comms(db, case)):
            comms_gen.generate(db, case, "clarification")
        to = S.NEEDS_INFO
        if case.status != to:
            _sys(db, case, to, "critical information missing: " + ", ".join(critical_missing))
        return
    result = _plan(db, case, constraints)
    conf = confidence.compute(
        field_confs=_ledger_confs(case), required=sum(for_category(case.category), []),
        agreement=case.analysis["risk"]["reconciled"]["agreement"],
        unresolved_conflicts=len(ledger.open_issues(case, "conflict")),
        review_verdict=result["review"].verdict if result["review"] else None, revised=result["revised"],
        citations_valid=result["citations_valid"], degraded=degraded_flag or result["source"] == "rules",
        system_unavailable=bool(ledger.open_issues(case, "system_unavailable")) or bool(result["tool_failures"]),
        planner_conf=result["plan"].confidence,
    )
    case.overall_confidence, case.confidence_breakdown = conf["overall"], conf
    block = runtime_settings.get(db, "conf_block")
    if conf["overall"] < block or (result["review"] and result["review"].verdict == "block"):
        ledger.open_issue(db, case, "low_confidence", f"Insufficient evidence for a safe recommendation (confidence {conf['overall']:.2f})",
                          severity="high", blocking=True, detail={"breakdown": conf}, dedupe_key="overall")
        _flag(case, "low_confidence")
    else:
        ledger.auto_resolve(db, case, "low_confidence", {i.detail.get("_key") for i in ledger.open_issues(case, "low_confidence")
                                                          if i.detail.get("_key", "").startswith("ungrounded")}, "confidence recovered")
        _flag(case, "low_confidence", False)
    if result["source"] == "rules":
        _flag(case, "degraded_mode")
    if first_run and not any(c.audience == "requester_ack" for c in _comms(db, case)):
        comms_gen.generate(db, case, "requester_ack")
    blocking = [i for i in ledger.open_issues(case) if i.blocking]
    needs_review = blocking or (case.flags or {}).get("degraded_mode")
    to = S.NEEDS_REVIEW if needs_review else S.READY_FOR_APPROVAL
    if case.status == S.ON_HOLD:
        _sys(db, case, S.ANALYZING, "re-analysis")
    if case.status != to and to in S.TRANSITIONS.get(case.status, set()):
        reason = "; ".join(i.title for i in blocking[:3]) if blocking else ("degraded mode" if needs_review else "analysis complete")
        _sys(db, case, to, reason)
    owner = db.get(User, case.assignee_id) if case.assignee_id else None
    outbox.notify(db, owner, f"[{case.ref}] Analysis ready: {case.risk_level} risk, confidence {conf['overall']:.2f}", kind="analysis", case=case)


def _comms(db: Session, case: Case):
    from app.models import Communication

    return db.scalars(select(Communication).where(Communication.case_id == case.id)).all()


def _ledger_confs(case: Case) -> dict[str, float]:
    return {f.key: (1.0 if f.human_value is not None else f.confidence if f.status != "missing" else 0.0) for f in case.facts}


def reanalyze(case_id: int, reason: str, constraints: dict[str, Any] | None = None) -> None:
    with session_scope() as db:
        case = db.get(Case, case_id)
        reanalyze_inline(db, case, reason, constraints)
        _mark(db, case, False)


def reanalyze_inline(db: Session, case: Case, reason: str, constraints: dict[str, Any] | None = None) -> None:
    case.processing = True
    db.commit()
    audit.record(db, action="analysis.rerun", summary=f"Re-analysis (ledger v{case.ledger_version}): {reason}", actor_type="system",
                 actor_id="orchestrator", case_id=case.id)
    if case.status in (S.NEEDS_INFO, S.NEEDS_REVIEW, S.READY_FOR_APPROVAL, S.ON_HOLD):
        _sys(db, case, S.ANALYZING, reason)
    analyze(db, case, constraints=constraints)
    case.processing = False
    db.commit()


def replan(case_id: int, constraints: dict[str, Any]) -> None:
    """After a rejection: plan again under the approver's constraints, then route for approval."""
    with session_scope() as db:
        case = db.get(Case, case_id)
        audit.record(db, action="analysis.replan", summary=f"Re-planning after rejection: {constraints.get('reason', '')}",
                     actor_type="system", actor_id="orchestrator", case_id=case.id, meta={"constraints": constraints})
        analyze(db, case, constraints=constraints)
        _mark(db, case, False)
