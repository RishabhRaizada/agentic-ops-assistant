"""Deterministic risk scoring. Runs alongside the Risk agent as an independent second opinion;
if the two disagree by two or more levels the case is flagged as a contradiction for a human."""

from typing import Any

LEVELS = ["low", "medium", "high", "critical"]


def score(facts: dict[str, Any], category: str) -> dict[str, Any]:
    pts = 0
    factors: list[str] = []
    days = facts.get("days_to_event")
    if days is not None:
        if days <= 7:
            pts += 3
            factors.append(f"event in {days} day(s) (+3)")
        elif days <= 14:
            pts += 2
            factors.append(f"event in {days} days (+2)")
        else:
            pts += 1
            factors.append(f"event in {days} days (+1)")
    req, short = facts.get("quantity_required"), facts.get("shortfall")
    if req and short is not None and req > 0:
        pct = short / req
        if pct >= 0.5:
            pts += 3
        elif pct >= 0.2:
            pts += 2
        elif pct > 0:
            pts += 1
        if pct > 0:
            factors.append(f"shortfall {short}/{req} units ({pct:.0%})")
    hrs = facts.get("hours_to_deadline")
    if hrs is not None:
        if hrs <= 8:
            pts += 2
            factors.append(f"decision due in {hrs:.1f} h (+2)")
        elif hrs <= 24:
            pts += 1
            factors.append(f"decision due in {hrs:.1f} h (+1)")
    if category == "customer_commitment" or facts.get("customer"):
        pts += 2
        factors.append("customer commitment at stake (+2)")
    event = (facts.get("event") or "").lower()
    if "launch" in event or "opening" in event:
        pts += 1
        factors.append("store launch (+1)")
    slip = facts.get("slip_days")
    if slip:
        factors.append(f"event slips {slip} day(s) without action")
    if pts >= 9:
        level = "critical"
    elif pts >= 6:
        level = "high"
    elif pts >= 4:
        level = "medium"
    else:
        level = "low"
    if pts >= 9 or (hrs is not None and hrs <= 24 and pts >= 6):
        urgency = "P1"
    elif pts >= 6:
        urgency = "P2"
    elif pts >= 4:
        urgency = "P3"
    else:
        urgency = "P4"
    return {"level": level, "urgency": urgency, "points": pts, "factors": factors}


def reconcile(llm_level: str | None, rules_level: str) -> dict[str, Any]:
    if llm_level is None:
        return {"final": rules_level, "gap": None, "agreement": 0.8, "contradiction": False, "basis": "rules only (risk agent unavailable)"}
    gap = abs(LEVELS.index(llm_level) - LEVELS.index(rules_level))
    final = LEVELS[max(LEVELS.index(llm_level), LEVELS.index(rules_level))]
    agreement = {0: 1.0, 1: 0.9}.get(gap, 0.6)
    return {
        "final": final,
        "gap": gap,
        "agreement": agreement,
        "contradiction": gap >= 2,
        "basis": "agent and rules agree" if gap == 0 else f"took the higher of agent={llm_level} / rules={rules_level}",
    }
