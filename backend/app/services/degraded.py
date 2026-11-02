"""Deterministic fallbacks used when every LLM is unavailable. Coverage is narrower than the agents,
but the case keeps moving: fields are regex-extracted with real evidence quotes, flagged as
degraded and routed to a human. This is the floor of the system, never its ceiling."""

import re

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agents.contracts import ExtractedField, IntakeResult, IssueExtraction
from app.models import Store, Supplier
from app.services.parsing import inr

_C = 0.5  # flat confidence: regexes are precise but blind to context


def _f(m: re.Match | None, group: int = 0, value: str | None = None) -> ExtractedField:
    if not m:
        return ExtractedField(value=None, status="missing", confidence=0.0)
    return ExtractedField(value=value or m.group(group), status="stated", confidence=_C, evidence_quote=m.group(0))


def extract(db: Session, subject: str, body: str) -> IntakeResult:
    text = f"{subject}\n{body}"
    qty_req = re.search(r"(\d[\d,]*)\s+(?:[\w\"'-]+\s){0,3}(?:units|pcs|pieces|nos|items|mats|kits|terminals|trolleys|tvs|fixtures)\b", text, re.I)
    avail = re.search(r"only\s+(\d[\d,]*)\s+(?:units\s+)?(?:are\s+|is\s+)?(?:currently\s+)?(?:available|in stock|in the warehouse|left)", text, re.I) \
        or re.search(r"(\d[\d,]*)\s+units\s+(?:are\s+)?(?:currently\s+)?available", text, re.I)
    delay = re.search(r"delayed\s+by\s+(\d+|\w+)\s+days?", text, re.I) or re.search(r"(\d+)[- ]day delay", text, re.I)
    deadline = re.search(r"by\s+(\d{1,2}(?::\d{2})?\s*(?:am|pm)\s*(?:today|tomorrow)?)", text, re.I) \
        or re.search(r"by\s+(eod|end of day|tomorrow)", text, re.I)
    event_date = re.search(r"(next week|tomorrow|in \d+ days|on \d{1,2} \w+)", text, re.I)
    event = re.search(r"(store launch|store opening|launch|opening|customer order)", text, re.I)
    cost = re.search(r"(?:₹|rs\.?|inr)\s?(\d[\d,.]*\s*(?:lakh|lac|crore|k)?)", text, re.I)
    item = None
    from app.models import Sku

    names = sorted(((n, s) for s in db.scalars(select(Sku)) for n in [s.name, *s.aliases]), key=lambda x: -len(x[0]))
    for n, _s in names:
        m = re.search(re.escape(n), text, re.I)
        if m:
            item = m
            break
    if item is None:
        item = re.search(r"\d[\d,]*\s+((?:[\w-]+\s){0,3}(?:units|fixtures|racks|displays|mats|kits))", text, re.I)

    location = None
    for s in db.scalars(select(Store)):
        m = re.search(re.escape(s.city), text, re.I)
        if m:
            location = m
            break
    supplier = None
    for sup in db.scalars(select(Supplier)):
        m = re.search(re.escape(sup.name.split()[0]), text, re.I)
        if m:
            supplier = m
            break
    supplier_word = re.search(r"\bour supplier\b|\bthe supplier\b|\bvendor\b", text, re.I)

    issue = IssueExtraction(
        category="delivery_delay" if delay or re.search(r"delay|late|slip", text, re.I) else "other",
        title=(subject or "Operational request")[:80],
        item=_f(item, 1 if item and item.groups() else 0),
        quantity_required=_f(qty_req, 1),
        quantity_available_claimed=_f(avail, 1),
        location=_f(location, 0),
        supplier=_f(supplier, 0) if supplier else ExtractedField(
            value=None, status="missing" if not supplier_word else "inferred", confidence=0.0 if not supplier_word else 0.2,
            evidence_quote=supplier_word.group(0) if supplier_word else None),
        delay_days=_f(delay, 1),
        event=_f(event, 1),
        event_date=_f(event_date, 1),
        decision_deadline=_f(deadline, 1),
        cost_mentioned=_f(cost, 1),
        customer=ExtractedField(value=None, status="missing", confidence=0.0),
        requested_action="Review manually: extracted by rules because the AI models were unavailable.",
    )
    return IntakeResult(
        in_scope=True,
        intent_clarity="ambiguous",
        summary=f"[Degraded mode] {subject or 'Request'}: fields extracted by deterministic rules; human review required.",
        issues=[issue],
        clarifying_questions=[],
    )


def plan(db: Session, case, deps, constraints: dict | None = None):
    """Deterministic option builder from system data (transfers, alternate supply, expedite).
    Used only when the Planner agent is unavailable; options are marked source=rules."""
    from app.agents.contracts import ActionOption, Plan
    from app.integrations import systems
    from app.services import ledger

    constraints = constraints or {}
    cap = constraints.get("budget_cap_inr")
    shortfall = ledger.value(case, "shortfall") or 0
    sku, store = case.sku_code, case.store_code
    if not sku or not store or shortfall <= 0:
        return Plan(options=[ActionOption(key="A", title="Manual assessment required", steps=["Review the request and source data manually"],
                                          est_cost_inr=0, cost_breakdown="n/a", covers_qty=0, eta_days=0, meets_deadline=False,
                                          feasibility="low", risks=["automated planning unavailable"], rationale="Insufficient structured data for a rules-based plan.")],
                    recommended_key="A", recommendation_rationale="Degraded mode: no automated recommendation.", confidence=0.2,
                    open_questions=["Confirm SKU, store and shortfall"])

    def call(tool, fn, **args):
        try:
            return deps.record(tool, args, data=fn())
        except systems.SystemUnavailable as e:
            return deps.record(tool, args, error=str(e))

    t_transfer = call("get_transfer_options", lambda: systems.get_transfer_options(db, sku, store), sku_code=sku, to_location=store)
    t_alt = call("list_alternate_suppliers", lambda: systems.list_alternate_suppliers(db, sku), sku_code=sku)
    t_exp = call("get_expedite_quotes", lambda: systems.get_expedite_quotes(db, sku), sku_code=sku)
    days = ledger.value(case, "days_to_event")
    options = []

    transfers = sorted(t_transfer.get("data") or [], key=lambda x: x["lead_days"])
    moved, t_cost, t_eta, steps = 0, 0.0, 0.0, []
    for lane in transfers:
        if moved >= shortfall:
            break
        q = min(lane["max_quantity"], shortfall - moved)
        moved += q
        t_cost += lane["fixed_cost_inr"] + q * lane["unit_premium_inr"]
        t_eta = max(t_eta, lane["lead_days"])
        steps.append(f"Raise transfer of {q} units from {lane['from_location']} to {store}")
    alts = sorted(t_alt.get("data") or [], key=lambda x: x["unit_premium_inr"])
    if alts and moved < shortfall:
        a = alts[0]
        q = min(a["max_quantity"], shortfall - moved)
        cost = t_cost + a["fixed_cost_inr"] + q * a["unit_premium_inr"]
        options.append(ActionOption(
            key="A", title=f"Transfers + {a['supplier_name']} for the balance", steps=[*steps, f"Order {q} units from {a['supplier_name']}"],
            est_cost_inr=cost, cost_breakdown=f"transfers {inr(t_cost)} + {q} × {inr(a['unit_premium_inr'])} + {inr(a['fixed_cost_inr'])}",
            covers_qty=moved + q, eta_days=max(t_eta, a["lead_days"]), meets_deadline=days is None or max(t_eta, a["lead_days"]) <= days,
            feasibility="medium", rationale="Rules-based: cheapest transfer lanes first, cheapest alternate supplier for the remainder.",
            tool_refs=[t_transfer["ref"], t_alt["ref"]]))
    if transfers:
        options.append(ActionOption(
            key="B", title="Partial launch with transfers only", steps=steps or ["No transfers available"], est_cost_inr=t_cost,
            cost_breakdown=f"transfer lanes {inr(t_cost)}", covers_qty=moved, eta_days=t_eta,
            meets_deadline=days is None or t_eta <= days, feasibility="high",
            risks=[f"covers {moved} of {shortfall} units"], rationale="Rules-based: lowest-cost partial cover.", tool_refs=[t_transfer["ref"]]))
    exps = t_exp.get("data") or []
    if exps:
        e = exps[0]
        q = min(e["max_quantity"], shortfall)
        options.append(ActionOption(
            key="C", title="Expedite from current supplier", steps=[f"Request expedited shipment of {q} units from {e['supplier_code']}"],
            est_cost_inr=e["fixed_cost_inr"] + q * e["unit_premium_inr"], cost_breakdown=f"{inr(e['fixed_cost_inr'])} + {q} × {inr(e['unit_premium_inr'])}",
            covers_qty=q, eta_days=e["lead_days"], meets_deadline=days is None or e["lead_days"] <= days, feasibility="medium",
            rationale="Rules-based: single-source expedite.", tool_refs=[t_exp["ref"]]))
    if cap is not None:
        options = [o for o in options if o.est_cost_inr <= cap] or options[-1:]
    if not options:
        options = [ActionOption(key="A", title="Escalate for manual sourcing", steps=["Escalate to the regional head"], est_cost_inr=0,
                                cost_breakdown="n/a", covers_qty=0, eta_days=0, meets_deadline=False, feasibility="low",
                                rationale="No system options found.")]
    best = max(options, key=lambda o: (o.meets_deadline, o.covers_qty, -o.est_cost_inr))
    return Plan(options=options, recommended_key=best.key, confidence=0.45,
                recommendation_rationale="Degraded mode (AI planner unavailable): options computed by deterministic rules. Verify before submitting.")
