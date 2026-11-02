"""The case fact ledger: the shared blackboard. Agents read facts from it (by ref) and cite them;
humans correct them; code derives numbers from them. AI and human values are stored side by side,
so a human correction is never overwritten by a later AI re-run."""

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Case, Fact, Issue

LABELS = {
    "item": "Item",
    "sku": "SKU (resolved)",
    "quantity_required": "Units required",
    "quantity_available_claimed": "Units available (per request)",
    "inventory_available": "Units available (inventory system)",
    "available_qty": "Units available (effective)",
    "shortfall": "Shortfall (units)",
    "location": "Location (as written)",
    "store": "Store (resolved)",
    "supplier": "Supplier",
    "delay_days": "Delay (days)",
    "event": "Business event",
    "event_date": "Event / required date",
    "launch_date": "Launch date (store calendar)",
    "decision_deadline": "Decision deadline",
    "cost_mentioned": "Cost mentioned",
    "customer": "Customer",
    "po": "Purchase order",
    "expected_arrival": "Expected arrival of delayed stock",
    "days_to_event": "Days until event",
    "slip_days": "Event slip if no action (days)",
    "daily_revenue": "Expected daily revenue (store plan)",
    "revenue_at_risk": "Revenue at risk (computed)",
    "hours_to_deadline": "Hours to decision deadline",
}


def get(case: Case, key: str) -> Fact | None:
    return next((f for f in case.facts if f.key == key), None)


def value(case: Case, key: str) -> Any:
    f = get(case, key)
    return f.value if f else None


def upsert(
    db: Session,
    case: Case,
    key: str,
    ai_value: Any,
    *,
    source: str,
    status: str = "stated",
    confidence: float = 1.0,
    raw_text: str | None = None,
    evidence_quote: str | None = None,
    grounded: bool | None = None,
) -> Fact:
    f = get(case, key)
    if f is None:
        n = len(case.facts) + 1
        f = Fact(
            case_id=case.id, ref=f"F{n}", key=key, label=LABELS.get(key, key), source=source,
            ai_value=ai_value, raw_text=raw_text, status=status, confidence=confidence,
            evidence_quote=evidence_quote, grounded=grounded,
        )
        db.add(f)
        case.facts.append(f)
    else:
        changed = f.ai_value != ai_value or f.status != status
        f.ai_value, f.source, f.status, f.confidence = ai_value, source if f.human_value is None else f.source, status, confidence
        f.raw_text, f.evidence_quote, f.grounded = raw_text, evidence_quote, grounded
        if changed:
            f.version += 1
    db.flush()
    return f


def snapshot(case: Case, keys: list[str] | None = None) -> list[dict[str, Any]]:
    """The context pack representation agents receive."""
    out = []
    for f in case.facts:
        if keys and f.key not in keys:
            continue
        if f.value is None and f.status == "missing":
            out.append({"ref": f.ref, "fact": f.label, "value": None, "status": "MISSING"})
            continue
        out.append({
            "ref": f.ref,
            "fact": f.label,
            "value": f.value,
            "source": "human correction" if f.human_value is not None else f.source,
            "status": f.status,
        })
    return out


def refs(case: Case) -> set[str]:
    return {f.ref for f in case.facts}


def open_issue(db: Session, case: Case, type_: str, title: str, *, severity: str = "medium",
               blocking: bool = False, detail: dict[str, Any] | None = None, dedupe_key: str | None = None) -> Issue:
    """Create an issue unless an open one with the same type+key already exists."""
    key = dedupe_key or title
    for i in case.issues:
        if i.status == "open" and i.type == type_ and (i.detail or {}).get("_key", i.title) == key:
            i.detail = {**(detail or {}), "_key": key}
            i.severity, i.blocking = severity, blocking
            return i
    issue = Issue(case_id=case.id, type=type_, title=title, severity=severity, blocking=blocking,
                  detail={**(detail or {}), "_key": key})
    db.add(issue)
    case.issues.append(issue)
    db.flush()
    return issue


def open_issues(case: Case, *types: str) -> list[Issue]:
    return [i for i in case.issues if i.status == "open" and (not types or i.type in types)]


def auto_resolve(db: Session, case: Case, type_: str, keep_keys: set[str], note: str) -> None:
    """Close system-generated issues of a type that the latest analysis no longer produces."""
    for i in case.issues:
        if i.status == "open" and i.type == type_ and (i.detail or {}).get("_key") not in keep_keys:
            i.status, i.resolution = "resolved", note
    db.flush()


def resolve_key(db: Session, case: Case, type_: str, key: str, note: str) -> None:
    from app.clock import now

    for i in case.issues:
        if i.status == "open" and i.type == type_ and (i.detail or {}).get("_key") == key:
            i.status, i.resolution, i.resolved_at = "resolved", note, now()
    db.flush()


def facts_by_key(db: Session, case_id: int) -> dict[str, Fact]:
    return {f.key: f for f in db.scalars(select(Fact).where(Fact.case_id == case_id))}
