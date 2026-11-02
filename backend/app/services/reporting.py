"""Management reporting. Every number is computed here from the database; the Reporter agent only
turns them into a narrative (it is told never to recompute or invent figures)."""

import json
from collections import Counter, defaultdict
from datetime import timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agents.base import load_prompt, run_agent
from app.agents.definitions import reporter_agent
from app.clock import local_now, now
from app.models import AgentRun, Approval, Case, Fact, Recommendation
from app.orchestrator import states as S
from app.services import audit, ledger

# Approximate public list prices (USD per 1M tokens) used only for the cost estimate on the dashboard.
PRICE_IN, PRICE_OUT, USD_INR = 0.30, 2.50, 84.0


def _hours(a, b) -> float | None:
    return round((b - a).total_seconds() / 3600, 2) if a and b else None


def _avg(xs: list[float]) -> float | None:
    xs = [x for x in xs if x is not None]
    return round(sum(xs) / len(xs), 2) if xs else None


def metrics(db: Session) -> dict[str, Any]:
    cases = db.scalars(select(Case)).all()
    live = [c for c in cases if c.status not in (S.MERGED,)]
    open_ = [c for c in live if c.status in S.OPEN]
    approvals = db.scalars(select(Approval)).all()
    pending = [a for a in approvals if a.status == "pending"]
    decided = [a for a in approvals if a.status in ("approved", "rejected") and a.decided_at]
    runs = db.scalars(select(AgentRun)).all()
    facts = db.scalars(select(Fact).where(Fact.source.in_(["email", "update"]), Fact.status != "missing")).all()
    recs_approved = db.scalars(select(Recommendation).where(Recommendation.status == "approved")).all()

    by_agent: dict[str, list[AgentRun]] = defaultdict(list)
    for r in runs:
        by_agent[r.agent].append(r)
    tokens_in = sum(r.tokens_in for r in runs)
    tokens_out = sum(r.tokens_out for r in runs)
    t = now()
    days = [(local_now() - timedelta(days=i)).date() for i in range(13, -1, -1)]
    vol = Counter(c.created_at.astimezone(local_now().tzinfo).date() for c in live)
    return {
        "generated_at": t.isoformat(),
        "totals": {
            "all": len(live), "open": len(open_),
            "awaiting_approval": len([c for c in live if c.status == S.AWAITING_APPROVAL]),
            "needs_attention": len([c for c in open_ if c.status in (S.NEEDS_REVIEW, S.NEEDS_INFO, S.READY_FOR_APPROVAL, S.ON_HOLD)]),
            "sla_breached": len([c for c in open_ if (c.flags or {}).get("sla_breached")]),
            "approvals_due_60m": len([a for a in pending if a.due_at <= t + timedelta(minutes=60)]),
            "escalated": len([c for c in open_ if (c.flags or {}).get("escalated")]),
            "closed": len([c for c in live if c.status == S.CLOSED]),
            "cancelled": len([c for c in live if c.status == S.CANCELLED]),
            "duplicates_merged": len([c for c in cases if c.status == S.MERGED]),
            "critical_open": len([c for c in open_ if c.risk_level == "critical"]),
        },
        "by_status": dict(Counter(c.status for c in live)),
        "by_category": dict(Counter(c.category for c in live)),
        "by_region": dict(Counter(c.region or "unknown" for c in live)),
        "by_risk": dict(Counter(c.risk_level or "unassessed" for c in open_)),
        "volume_14d": [{"date": d.isoformat(), "count": vol.get(d, 0)} for d in days],
        "cycle_times_h": {
            "intake_to_triage": _avg([_hours(c.created_at, c.triaged_at) for c in live]),
            "intake_to_decision": _avg([_hours(c.created_at, c.approved_at) for c in live]),
            "intake_to_close": _avg([_hours(c.created_at, c.closed_at) for c in live]),
            "approval_turnaround": _avg([_hours(a.created_at, a.decided_at) for a in decided]),
        },
        "sla": {
            "decided": len(decided),
            "decided_on_time": len([a for a in decided if a.decided_at <= a.due_at]),
            "on_time_rate": round(len([a for a in decided if a.decided_at <= a.due_at]) / len(decided), 3) if decided else None,
            "escalations": len([a for a in approvals if a.status == "escalated"]),
        },
        "ai_quality": {
            "agent_runs": len(runs),
            "success_rate": round(len([r for r in runs if r.status == "ok"]) / len(runs), 3) if runs else None,
            "fallback_rate": round(len([r for r in runs if r.fallback_used]) / len(runs), 3) if runs else None,
            "degraded_runs": len([r for r in runs if r.degraded]),
            "validation_retry_rate": round(len([r for r in runs if r.validation_retries]) / len(runs), 3) if runs else None,
            "extracted_fields": len(facts),
            "human_override_rate": round(len([f for f in facts if f.human_value is not None]) / len(facts), 3) if facts else None,
            "ungrounded_rate": round(len([f for f in facts if f.grounded is False]) / len(facts), 3) if facts else None,
            "ai_recommendation_acceptance": round(len([r for r in recs_approved if r.is_recommended]) / len(recs_approved), 3) if recs_approved else None,
            "injection_flags": len([c for c in cases if (c.flags or {}).get("injection_suspected")]),
            "tokens_in": tokens_in, "tokens_out": tokens_out,
            "est_cost_inr": round((tokens_in * PRICE_IN + tokens_out * PRICE_OUT) / 1e6 * USD_INR, 2),
            "per_agent": {a: {"runs": len(rs), "avg_latency_ms": _avg([r.latency_ms for r in rs]),
                              "failures": len([r for r in rs if r.status != "ok"]),
                              "fallbacks": len([r for r in rs if r.fallback_used])} for a, rs in sorted(by_agent.items())},
        },
    }


def _render(out, metrics_used: dict[str, Any] | None = None) -> dict[str, Any]:
    return {"headline": out.headline, "bullets": out.bullets, "risks": out.risks, "asks": out.asks}


def case_brief(db: Session, case: Case) -> dict[str, Any]:
    payload = {
        "case": {"ref": case.ref, "title": case.title, "status": case.status, "risk": case.risk_level, "priority": case.priority,
                 "confidence": case.overall_confidence, "summary": case.summary},
        "facts": ledger.snapshot(case),
        "decision": [r.option for r in db.scalars(select(Recommendation).where(Recommendation.case_id == case.id, Recommendation.status == "approved"))],
        "key_events": [e.summary for e in db.scalars(select(audit.AuditEvent).where(audit.AuditEvent.case_id == case.id, audit.AuditEvent.pinned.is_(True)))],
        "open_issues": [i.title for i in ledger.open_issues(case)],
    }
    out = run_agent(db, agent=reporter_agent, name="reporter", step="case_brief", prompt=load_prompt("reporter"),
                    user_prompt=json.dumps(payload, default=str), input_payload=payload, case_id=case.id, ledger_version=case.ledger_version,
                    confidence_of=lambda o: None)
    if out.ok:
        rep = _render(out.output)
        rep["generated_by"] = "ai"
    else:
        rep = {"headline": f"{case.ref}: {case.title} - {case.status.replace('_', ' ').lower()}",
               "bullets": [f"Risk {case.risk_level or 'n/a'}, priority {case.priority or 'n/a'}", *payload["key_events"][-5:]],
               "risks": payload["open_issues"], "asks": [], "generated_by": "template"}
    _note_brief(case, rep)
    return rep


def _note_brief(case: Case, rep: dict) -> None:
    case.analysis = {**(case.analysis or {}), "brief": rep}


def portfolio_digest(db: Session) -> dict[str, Any]:
    m = metrics(db)
    top = [c for c in db.scalars(select(Case).where(Case.status.in_(S.OPEN)))]
    top.sort(key=lambda c: (["critical", "high", "medium", "low", None].index(c.risk_level), c.created_at))
    payload = {
        "metrics": {k: m[k] for k in ("totals", "by_risk", "cycle_times_h", "sla")},
        "ai_quality": {k: m["ai_quality"][k] for k in ("human_override_rate", "fallback_rate", "ai_recommendation_acceptance", "injection_flags")},
        "top_open_cases": [{"ref": c.ref, "title": c.title, "status": c.status, "risk": c.risk_level, "store": c.store_code,
                            "revenue_at_risk_inr": ledger.value(c, "revenue_at_risk")} for c in top[:6]],
        "as_of": local_now().strftime("%d %b %Y %H:%M IST"),
    }
    out = run_agent(db, agent=reporter_agent, name="reporter", step="portfolio_digest", prompt=load_prompt("reporter"),
                    user_prompt=json.dumps(payload, default=str), input_payload=payload, case_id=None, confidence_of=lambda o: None)
    if out.ok:
        rep = {**_render(out.output), "generated_by": "ai"}
    else:
        t = m["totals"]
        rep = {"headline": f"{t['open']} open requests, {t['critical_open']} critical, {t['awaiting_approval']} awaiting approval",
               "bullets": [f"{c['ref']} {c['title']} ({c['risk']})" for c in payload["top_open_cases"]],
               "risks": [f"{t['sla_breached']} SLA breach(es)"] if t["sla_breached"] else [], "asks": [], "generated_by": "template"}
    rep["metrics"] = payload
    audit.record(db, action="report.generated", summary=f"Management digest generated ({rep['generated_by']})", actor_type="ai" if out.ok else "system",
                 actor_id="reporter-agent")
    return rep
