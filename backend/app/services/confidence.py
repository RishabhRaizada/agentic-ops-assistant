"""Composite, explainable confidence. A model's self-reported confidence is only one input and is
capped by evidence: grounding of extracted fields, agreement between the risk agent and the rules
engine, unresolved conflicts, the reviewer's verdict and citation validity."""

from typing import Any

REQUIRED_WEIGHTS = {
    "item": 1.0, "quantity_required": 1.5, "location": 1.5, "supplier": 0.5,
    "event_date": 1.0, "decision_deadline": 0.5, "delay_days": 0.5, "customer": 0.5,
}
GROUNDING = {"grounded": 1.0, "inferred": 0.6, "ungrounded": 0.3, "missing": 0.0}


def field_conf(llm_conf: float, status: str, grounded: bool | None) -> float:
    if status == "missing":
        return 0.0
    if status == "inferred":
        return min(llm_conf, GROUNDING["inferred"])
    return min(llm_conf, GROUNDING["grounded"] if grounded else GROUNDING["ungrounded"])


def compute(
    *,
    field_confs: dict[str, float],
    required: list[str],
    agreement: float,
    unresolved_conflicts: int,
    review_verdict: str | None,
    revised: bool,
    citations_valid: bool,
    degraded: bool,
    system_unavailable: bool,
    planner_conf: float | None,
) -> dict[str, Any]:
    weights = {k: REQUIRED_WEIGHTS.get(k, 0.5) for k in required} or {"item": 1.0}
    extraction = sum(field_confs.get(k, 0.0) * w for k, w in weights.items()) / sum(weights.values())
    reconciliation = 0.7 ** unresolved_conflicts
    if system_unavailable:
        reconciliation *= 0.8
    review = {"pass": 0.85 if revised else 1.0, "revise": 0.7, "block": 0.4}.get(review_verdict or "", 0.75)
    citations = 1.0 if citations_valid else 0.5
    overall = extraction * agreement * reconciliation * review * citations
    if planner_conf is not None:
        overall = min(overall, planner_conf + 0.15)  # an unsure planner caps the overall score
    if degraded:
        overall = min(overall, 0.55)
    overall = round(max(0.0, min(1.0, overall)), 3)
    band = "high" if overall >= 0.75 else "medium" if overall >= 0.5 else "low"
    return {
        "overall": overall,
        "band": band,
        "components": {
            "extraction": round(extraction, 3),
            "risk_agreement": round(agreement, 3),
            "reconciliation": round(reconciliation, 3),
            "review": round(review, 3),
            "citations": citations,
        },
        "notes": [
            n for n in [
                f"{unresolved_conflicts} unresolved data conflict(s)" if unresolved_conflicts else None,
                "a source system was unavailable; values unverified" if system_unavailable else None,
                "degraded mode (LLM unavailable): capped at 0.55" if degraded else None,
                "reviewer requested a revision" if revised else None,
                f"reviewer verdict: {review_verdict}" if review_verdict and review_verdict != "pass" else None,
            ] if n
        ],
    }
