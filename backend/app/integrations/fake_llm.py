"""Deterministic stand-in for Gemini, used by the test suite and for offline development
(LLM_FAKE=true). It speaks the same protocol as a real model through PydanticAI's FunctionModel:
it calls the planner's tools, returns schema-valid outputs, and can be scripted per agent to
produce specific behaviours (contradictions, low confidence, bad citations). Runs are recorded
with model name 'fake-llm' so they can never be mistaken for real model output."""

import json
import re
from collections.abc import Callable
from typing import Any

from pydantic_ai.messages import ModelRequest, ModelResponse, ToolCallPart, ToolReturnPart, UserPromptPart
from pydantic_ai.models.function import AgentInfo, FunctionModel

Overrides = dict[str, Callable[[str, AgentInfo], dict[str, Any]]]


def _prompt(messages) -> str:
    for m in messages:
        if isinstance(m, ModelRequest):
            for p in m.parts:
                if isinstance(p, UserPromptPart):
                    return p.content if isinstance(p.content, str) else str(p.content)
    return ""


def _tool_returns(messages) -> dict[str, Any]:
    out = {}
    for m in messages:
        if isinstance(m, ModelRequest):
            for p in m.parts:
                if isinstance(p, ToolReturnPart):
                    out[p.tool_name] = p.content
    return out


def _email(prompt: str) -> str:
    m = re.search(r"<untrusted_email>\n(.*?)\n</untrusted_email>", prompt, re.S)
    return (m.group(1) if m else prompt).replace("^", " ")


def _field(value: str | None, quote: str | None = None, conf: float = 0.9) -> dict[str, Any]:
    if not value:
        return {"value": None, "status": "missing", "confidence": 0.0, "evidence_quote": None}
    return {"value": value, "status": "stated", "confidence": conf, "evidence_quote": quote or value}


def intake(prompt: str, info: AgentInfo) -> dict[str, Any]:
    from app.db import SessionLocal
    from app.services import degraded

    text = _email(prompt)
    subject = re.search(r"Subject: (.*)", text)
    body = text.split("\n\n", 1)[1] if "\n\n" in text else text
    with SessionLocal() as db:
        res = degraded.extract(db, subject.group(1) if subject else "", body)
    issue = res.issues[0].model_dump()
    for v in issue.values():
        if isinstance(v, dict) and v.get("status") == "stated":
            v["confidence"] = 0.92
    lower = text.lower()
    issues = [issue]
    clarity = "clear"
    if re.search(r"\n\s*2\.", body):
        clarity = "multi_issue"
        second = json.loads(json.dumps(issue))
        second["title"] = "Second issue from the same email"
        second["category"] = "customer_commitment"
        issues.append(second)
    if "postponed" in lower:
        issue["event"] = _field("store launch", "store launch")
    if "newsletter" in lower or "unsubscribe" in lower:
        return {"in_scope": False, "intent_clarity": "clear", "summary": "Newsletter", "issues": [issue]}
    if issue["item"]["status"] == "missing" and issue["quantity_required"]["status"] == "missing":
        clarity = "ambiguous"
    issue["requested_action"] = "Assess impact and propose alternatives"
    return {"in_scope": True, "intent_clarity": clarity, "summary": f"Request: {issue['title']}", "issues": issues,
            "clarifying_questions": ["Which item and how many units?"] if clarity == "ambiguous" else []}


def guard(prompt: str, info: AgentInfo) -> dict[str, Any]:
    t = _email(prompt).lower()
    if "ignore all previous" in t or "system:" in t:
        return {"label": "malicious", "signals": ["Ignore all previous instructions"], "confidence": 0.95}
    return {"label": "safe", "signals": [], "confidence": 0.9}


def correlate(prompt: str, info: AgentInfo) -> dict[str, Any]:
    refs = re.findall(r'"ref": "(OPS-\d+)"', prompt)
    t = _email(prompt).lower()
    if not refs:
        return {"relation": "NEW", "confidence": 0.9, "rationale": "no candidates"}
    if "postponed" in t:
        return {"relation": "UPDATE", "target_case_ref": refs[0], "changed_fields": ["event_date"], "cancellation_signal": True,
                "change_summary": "launch postponed by 3 weeks", "confidence": 0.9, "rationale": "same launch, postponed"}
    if "instead of" in t or "revised" in t:
        return {"relation": "UPDATE", "target_case_ref": refs[0], "changed_fields": ["delay_days"],
                "change_summary": "delay revised from 10 to 6 days", "confidence": 0.9, "rationale": "supplier revised the delay"}
    return {"relation": "DUPLICATE", "target_case_ref": refs[0], "confidence": 0.92, "rationale": "same item, store and delay"}


def risk(prompt: str, info: AgentInfo) -> dict[str, Any]:
    refs = re.findall(r'"ref": "(F\d+)"', prompt) or ["F1"]
    return {"urgency": "P1", "risk_level": "high", "business_impact": "Launch at risk without intervention.",
            "drivers": [{"factor": "shortfall before launch", "fact_refs": refs[:2]}], "confidence": 0.85}


def planner_response(messages, info: AgentInfo) -> ModelResponse:
    returns = _tool_returns(messages)
    prompt = _prompt(messages)
    task = json.loads(re.search(r"<task>(.*?)</task>", prompt, re.S).group(1))
    sku, store = task.get("sku_code"), task.get("store_code")
    if not returns:
        return ModelResponse(parts=[
            ToolCallPart("get_transfer_options", {"sku_code": sku, "to_location": store}),
            ToolCallPart("list_alternate_suppliers", {"sku_code": sku}),
            ToolCallPart("get_expedite_quotes", {"sku_code": sku}),
        ])
    constraints = json.loads(re.search(r"<constraints>(.*?)</constraints>", prompt, re.S).group(1) or "{}")
    shortfall = task.get("shortfall_units") or 0
    tr, alt, exp = returns.get("get_transfer_options", {}), returns.get("list_alternate_suppliers", {}), returns.get("get_expedite_quotes", {})
    opts = []
    lanes = tr.get("data") or []
    moved = sum(lane["max_quantity"] for lane in lanes)
    tcost = sum(lane["fixed_cost_inr"] for lane in lanes)
    if alt.get("data"):
        a = alt["data"][0]
        q = max(0, min(a["max_quantity"], shortfall - moved))
        opts.append({"key": "A", "title": "Transfers plus alternate supplier", "steps": ["Raise transfers", f"Order {q} from {a['supplier_code']}"],
                     "est_cost_inr": tcost + a["fixed_cost_inr"] + q * a["unit_premium_inr"], "cost_breakdown": "transfers + premium",
                     "covers_qty": moved + q, "eta_days": 4, "meets_deadline": True, "feasibility": "high", "rationale": "full cover",
                     "tool_refs": [r for r in (tr.get("ref"), alt.get("ref")) if r and "data" in returns.get(_name(r, returns), {})]})
    if lanes:
        opts.append({"key": "B", "title": "Partial launch with transfers only", "steps": ["Raise transfers"], "est_cost_inr": tcost,
                     "cost_breakdown": "transfer lanes", "covers_qty": moved, "eta_days": 3, "meets_deadline": True,
                     "feasibility": "high", "rationale": "cheapest", "tool_refs": [tr["ref"]]})
    if exp.get("data"):
        e = exp["data"][0]
        q = min(e["max_quantity"], shortfall)
        opts.append({"key": "C", "title": "Expedite from current supplier", "steps": ["Request air freight"],
                     "est_cost_inr": e["fixed_cost_inr"] + q * e["unit_premium_inr"], "cost_breakdown": "expedite quote",
                     "covers_qty": q, "eta_days": e["lead_days"], "meets_deadline": True, "feasibility": "medium",
                     "rationale": "single source", "tool_refs": [exp["ref"]]})
    cap = constraints.get("budget_cap_inr")
    rejected = [t.lower() for t in constraints.get("rejected_titles", [])]
    opts = [o for o in opts if (cap is None or o["est_cost_inr"] <= cap) and o["title"].lower() not in rejected]
    conf = 0.85
    if not opts:
        opts = [{"key": "A", "title": "Source manually", "steps": ["Contact local vendors"], "est_cost_inr": 0,
                 "cost_breakdown": "unknown", "covers_qty": 0, "eta_days": 0, "meets_deadline": False, "feasibility": "low",
                 "rationale": "no system data for this item", "tool_refs": []}]
        conf = 0.3
    for o in opts:
        o["tool_refs"] = [r for r in o["tool_refs"] if r]
    plan = {"options": opts, "recommended_key": opts[0]["key"], "recommendation_rationale": "best cover before the event",
            "confidence": conf}
    return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name, plan)])


def _name(ref: str, returns: dict[str, Any]) -> str:
    return next((k for k, v in returns.items() if isinstance(v, dict) and v.get("ref") == ref), "")


def reviewer(prompt: str, info: AgentInfo) -> dict[str, Any]:
    return {"verdict": "pass", "issues": [], "summary": "Costs match the cited quotes.", "confidence": 0.9}


def comms(prompt: str, info: AgentInfo) -> dict[str, Any]:
    ref = re.search(r'"case_ref": "([^"]+)"', prompt)
    return {"subject": f"Update on {ref.group(1) if ref else 'your request'}", "body": "Hello,\n\nWe are on it.\n\nRegards,\nOperations Team",
            "fact_refs": re.findall(r'"ref": "(F\d+)"', prompt)[:3]}


def reporter(prompt: str, info: AgentInfo) -> dict[str, Any]:
    return {"headline": "Operations summary", "bullets": ["All figures as provided."], "risks": [], "asks": []}


HANDLERS: dict[str, Callable[[str, AgentInfo], dict[str, Any]]] = {
    "IntakeResult": intake, "GuardVerdict": guard, "Correlation": correlate, "RiskAssessment": risk,
    "Review": reviewer, "Draft": comms, "Report": reporter,
}


def make(overrides: Overrides | None = None, name: str = "fake-llm") -> FunctionModel:
    overrides = overrides or {}

    def fn(messages, info: AgentInfo) -> ModelResponse:
        schema = info.output_tools[0].parameters_json_schema if info.output_tools else {}
        title = schema.get("title", "")
        if title in overrides:
            return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name, overrides[title](_prompt(messages), info))])
        if title == "Plan":
            return planner_response(messages, info)
        handler = HANDLERS.get(title)
        if handler is None:
            raise RuntimeError(f"fake-llm has no handler for {title}")
        return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name, handler(_prompt(messages), info))])

    return FunctionModel(fn, model_name=name)
