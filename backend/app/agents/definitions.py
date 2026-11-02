"""Agent definitions. Each agent is a PydanticAI Agent with a typed output contract. Output
validators enforce grounding rules and raise ModelRetry, so a model that cites a fact that doesn't
exist, or quotes text that isn't in the email, has to correct itself before its answer is accepted."""

import threading
from dataclasses import dataclass, field
from typing import Any

from pydantic_ai import Agent, ModelRetry, RunContext
from sqlalchemy.orm import Session

from app.agents.contracts import (
    Correlation,
    Draft,
    GuardVerdict,
    IntakeResult,
    Plan,
    Report,
    Review,
    RiskAssessment,
)
from app.integrations import systems
from app.services.parsing import is_substring

# ----------------------------------------------------------------------------- deps


@dataclass
class IntakeDeps:
    email_text: str


@dataclass
class RefDeps:
    fact_refs: set[str]
    tool_refs: set[str] = field(default_factory=set)
    candidate_refs: set[str] = field(default_factory=set)


@dataclass
class PlannerDeps:
    db: Session
    fact_refs: set[str]
    sku_code: str | None
    store_code: str | None
    dc_code: str | None
    budget_cap_inr: float | None = None
    rejected_titles: list[str] = field(default_factory=list)
    tool_results: dict[str, dict[str, Any]] = field(default_factory=dict)

    def record(self, tool: str, args: dict[str, Any], data: Any = None, error: str | None = None) -> dict[str, Any]:
        ref = f"T{len(self.tool_results) + 1}"
        entry = {"ref": ref, "tool": tool, "args": args}
        if error:
            entry["error"] = error
        else:
            entry["data"] = data
        self.tool_results[ref] = entry
        return entry

    @property
    def ok_refs(self) -> set[str]:
        return {r for r, e in self.tool_results.items() if "error" not in e}


# ----------------------------------------------------------------------------- guard

guard_agent = Agent(output_type=GuardVerdict, name="guard", retries=1)

# ----------------------------------------------------------------------------- intake

intake_agent = Agent(output_type=IntakeResult, deps_type=IntakeDeps, name="intake", retries=2)

_FIELDS = [
    "item", "quantity_required", "quantity_available_claimed", "location", "supplier", "delay_days",
    "event", "event_date", "decision_deadline", "cost_mentioned", "customer",
]


@intake_agent.output_validator
def _intake_grounded(ctx: RunContext[IntakeDeps], out: IntakeResult) -> IntakeResult:
    bad = []
    for i, issue in enumerate(out.issues):
        for name in _FIELDS:
            f = getattr(issue, name)
            if f.status == "stated" and f.value and not is_substring(f.evidence_quote, ctx.deps.email_text):
                bad.append(f"issues[{i}].{name}")
    # One correction round; after that accept and let deterministic grounding cap confidence.
    if bad and ctx.retry < 1:
        raise ModelRetry(
            "These 'stated' fields have an evidence_quote that is not an exact substring of the email: "
            + ", ".join(bad)
            + ". Copy the exact words from the email (normal spaces), or mark the field inferred/missing."
        )
    return out


# ----------------------------------------------------------------------------- correlation

correlate_agent = Agent(output_type=Correlation, deps_type=RefDeps, name="correlation", retries=2)


@correlate_agent.output_validator
def _correlate_valid(ctx: RunContext[RefDeps], out: Correlation) -> Correlation:
    if out.relation != "NEW" and out.target_case_ref not in ctx.deps.candidate_refs:
        raise ModelRetry(f"target_case_ref must be one of {sorted(ctx.deps.candidate_refs)} when relation is {out.relation}")
    return out


# ----------------------------------------------------------------------------- risk

risk_agent = Agent(output_type=RiskAssessment, deps_type=RefDeps, name="risk", retries=2)


@risk_agent.output_validator
def _risk_refs(ctx: RunContext[RefDeps], out: RiskAssessment) -> RiskAssessment:
    unknown = sorted({r for d in out.drivers for r in d.fact_refs} - ctx.deps.fact_refs)
    if unknown:
        raise ModelRetry(f"Drivers cite fact refs that do not exist: {unknown}. Valid refs: {sorted(ctx.deps.fact_refs)}")
    return out


# ----------------------------------------------------------------------------- planner (tool-using)

planner_agent = Agent(output_type=Plan, deps_type=PlannerDeps, name="planner", retries=2)


_tool_lock = threading.Lock()


def _call(ctx: RunContext[PlannerDeps], tool: str, args: dict[str, Any], fn) -> dict[str, Any]:
    # Tools share the pipeline's DB session, which is not thread-safe: run them one at a time.
    with _tool_lock:
        try:
            data = fn()
        except systems.SystemUnavailable as e:
            return ctx.deps.record(tool, args, error=str(e))
        # Keep tool output small before it reaches the model (HolmesGPT pattern).
        if isinstance(data, list) and len(data) > 12:
            data = data[:12]
        return ctx.deps.record(tool, args, data=data)


@planner_agent.tool(sequential=True)
def get_inventory(ctx: RunContext[PlannerDeps], sku_code: str, location_code: str | None = None) -> dict[str, Any]:
    """Current stock for a SKU, optionally at one location (store or DC code). Returns on_hand,
    reserved, available and transferable units per location."""
    return _call(ctx, "get_inventory", {"sku_code": sku_code, "location_code": location_code},
                 lambda: systems.get_inventory(ctx.deps.db, sku_code, location_code))


@planner_agent.tool(sequential=True)
def get_transfer_options(ctx: RunContext[PlannerDeps], sku_code: str, to_location: str) -> dict[str, Any]:
    """Inter-store / DC transfer lanes into a destination store: max transferable units, lead days, costs."""
    return _call(ctx, "get_transfer_options", {"sku_code": sku_code, "to_location": to_location},
                 lambda: systems.get_transfer_options(ctx.deps.db, sku_code, to_location))


@planner_agent.tool(sequential=True)
def list_alternate_suppliers(ctx: RunContext[PlannerDeps], sku_code: str) -> dict[str, Any]:
    """Alternate suppliers that can supply this SKU: capacity, lead days, unit premium over contract price, fixed cost."""
    return _call(ctx, "list_alternate_suppliers", {"sku_code": sku_code},
                 lambda: systems.list_alternate_suppliers(ctx.deps.db, sku_code))


@planner_agent.tool(sequential=True)
def get_expedite_quotes(ctx: RunContext[PlannerDeps], sku_code: str) -> dict[str, Any]:
    """Quotes from the current supplier to expedite (e.g. air freight / split shipment) part of the order."""
    return _call(ctx, "get_expedite_quotes", {"sku_code": sku_code},
                 lambda: systems.get_expedite_quotes(ctx.deps.db, sku_code))


@planner_agent.tool(sequential=True)
def get_purchase_orders(ctx: RunContext[PlannerDeps], sku_code: str, dest_location: str | None = None) -> dict[str, Any]:
    """Open purchase orders for a SKU (supplier, quantity, promised and revised dates)."""
    return _call(ctx, "get_purchase_orders", {"sku_code": sku_code, "dest_location": dest_location},
                 lambda: systems.get_purchase_orders(ctx.deps.db, sku_code, dest_location))


@planner_agent.output_validator
def _plan_valid(ctx: RunContext[PlannerDeps], out: Plan) -> Plan:
    problems = []
    keys = [o.key for o in out.options]
    if out.recommended_key not in keys:
        problems.append(f"recommended_key '{out.recommended_key}' is not one of the option keys {keys}")
    if len(set(keys)) != len(keys):
        problems.append("option keys must be unique")
    ok_refs = ctx.deps.ok_refs
    for o in out.options:
        unknown_tools = sorted(set(o.tool_refs) - ok_refs)
        if unknown_tools:
            problems.append(f"option {o.key} cites tool refs that do not exist or failed: {unknown_tools}")
        unknown_facts = sorted(set(o.fact_refs) - ctx.deps.fact_refs)
        if unknown_facts:
            problems.append(f"option {o.key} cites unknown fact refs {unknown_facts}")
        if o.est_cost_inr > 0 and not o.tool_refs:
            problems.append(f"option {o.key} has a cost but cites no tool result; costs must come from tools")
        if ctx.deps.budget_cap_inr is not None and o.est_cost_inr > ctx.deps.budget_cap_inr:
            problems.append(f"option {o.key} costs {o.est_cost_inr:.0f} which exceeds the budget cap {ctx.deps.budget_cap_inr:.0f}")
        for t in ctx.deps.rejected_titles:
            if o.title.strip().lower() == t.strip().lower():
                problems.append(f"option {o.key} repeats the rejected option '{t}' unchanged")
    if problems and ctx.retry < 2:
        raise ModelRetry("Fix these problems: " + "; ".join(problems))
    if problems:
        # Out of retries: drop only the invalid options; the confidence calculator penalises this.
        bad_keys = {p.split()[1] for p in problems if p.startswith("option ")}
        kept = [o for o in out.options if o.key not in bad_keys]
        if not kept:
            raise ModelRetry("No valid options remain")
        out.options = kept
        if out.recommended_key not in [o.key for o in kept]:
            out.recommended_key = kept[0].key
        out.confidence = min(out.confidence, 0.45)
    return out


# ----------------------------------------------------------------------------- reviewer / comms / reporter

reviewer_agent = Agent(output_type=Review, name="reviewer", retries=1)
comms_agent = Agent(output_type=Draft, deps_type=RefDeps, name="comms", retries=1)
reporter_agent = Agent(output_type=Report, name="reporter", retries=1)


@comms_agent.output_validator
def _comms_refs(ctx: RunContext[RefDeps], out: Draft) -> Draft:
    if "[" in out.body and "]" in out.body and ctx.retry < 1:
        raise ModelRetry("Remove placeholders in square brackets; use only provided facts.")
    out.fact_refs = [r for r in out.fact_refs if r in ctx.deps.fact_refs]
    return out
