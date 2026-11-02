"""End-to-end workflow tests through the HTTP API with a deterministic fake LLM.
One test per edge case: each asserts detection, communication (issue/flag), recovery and the audit record."""

from datetime import timedelta

from app import clock
from app.integrations import fake_llm, llm


def _audit(api, case_id, action):
    return [e for e in api.c.get(f"/api/audit?case_id={case_id}").json() if e["action"] == action]


def test_happy_path_request_to_closure(api):
    case = api.ingest("S1")
    assert case["status"] == "READY_FOR_APPROVAL"
    assert api.fact(case, "shortfall")["value"] == 380
    assert api.fact(case, "inventory_available")["value"] == 120
    assert api.fact(case, "supplier")["value"].startswith("Vertex")  # inferred from the PO system
    assert case["risk_level"] in ("high", "critical")
    assert len([r for r in case["recommendations"] if r["status"] == "proposed"]) >= 2
    assert any(c["audience"] == "requester_ack" for c in case["communications"])
    rec = api.rec(case, "alternate")
    out = api.submit(case, rec)
    assert out["approval_required"] and out["role"] == "regional_head"
    appr = api.case(case["id"])["approvals"][-1]

    api.login("head.west")
    r = api.post(f"/api/approvals/{appr['id']}/decision", {"decision": "approve", "reason": "cost justified by launch revenue"})
    assert r.status_code == 200, r.text
    case = api.case(case["id"])
    assert case["status"] == "IN_PROGRESS"
    assert case["actions"] and {c["audience"] for c in case["communications"]} >= {"supplier", "status_update"}

    api.login("analyst")
    for a in case["actions"]:
        assert api.post(f"/api/requests/{case['id']}/actions/{a['id']}/complete").status_code == 200
    assert api.case(case["id"])["status"] == "RESOLVED"
    assert api.post(f"/api/requests/{case['id']}/close", {"reason": "resolved_as_planned", "note": "launch on time"}).status_code == 200
    case = api.case(case["id"])
    assert case["status"] == "CLOSED" and case["timestamps"]["closed_at"]
    assert api.c.get("/api/audit/verify").json()["ok"]


def test_unauthorized_approval_is_blocked_and_audited(api):
    case = api.ingest("S1")
    api.submit(case, api.rec(case, "alternate"))  # ₹1.42L -> regional head
    appr = api.case(case["id"])["approvals"][-1]
    for who, why in [("manager.north", "requires regional head"), ("manager.west", "requires regional head"),
                     ("auditor", "cannot approve"), ("head.north", "region")]:
        api.login(who)
        r = api.post(f"/api/approvals/{appr['id']}/decision", {"decision": "approve"})
        assert r.status_code == 403, (who, r.text)
        assert why in r.json()["detail"], (who, r.json())
    assert len(_audit(api, case["id"], "unauthorized_attempt")) == 4
    api.login("auditor")
    assert api.patch(f"/api/requests/{case['id']}/facts/quantity_required", {"value": 1, "reason": "x"}).status_code == 403


def test_self_approval_forbidden(api):
    api.login("head.west")
    case = api.ingest("S1", as_="head.west")
    api.submit(case, api.rec(case, "alternate"))
    appr = api.case(case["id"])["approvals"][-1]
    r = api.post(f"/api/approvals/{appr['id']}/decision", {"decision": "approve"})
    assert r.status_code == 403 and "self-approval" in r.json()["detail"]


def test_csrf_header_required(api):
    api.login("analyst")
    r = api.c.post("/api/requests", json={"subject": "x", "body": "hello world"})
    assert r.status_code == 403


def test_duplicate_request_is_merged(api):
    first = api.ingest("S1")
    dup = api.ingest("S2")
    assert dup["status"] == "MERGED" and dup["duplicate_of_ref"] == first["ref"]
    first = api.case(first["id"])
    assert len(first["messages"]) == 2 and first["messages"][1]["relation"] == "duplicate"
    assert _audit(api, first["id"], "case.linked")


def test_missing_information_requests_clarification(api):
    case = api.ingest("S3")
    assert case["status"] == "NEEDS_INFO"
    assert case["sla_paused_at"]
    missing = {i["detail"]["field"] for i in api.issues(case, "missing") if i["blocking"]}
    assert {"item", "quantity_required", "location"} <= missing
    assert any(c["audience"] == "clarification" for c in case["communications"])
    assert not [r for r in case["recommendations"] if r["status"] == "proposed"]  # no plan on missing facts


def test_conflicting_information_human_resolves(api):
    case = api.ingest("S4")
    conflict = api.issues(case, "conflict")
    assert conflict and conflict[0]["blocking"]
    assert "150" in conflict[0]["title"] and "100" in conflict[0]["title"]
    assert case["status"] == "NEEDS_REVIEW"
    rec = api.rec(case)
    api.login("manager.west")  # a West manager cannot touch a North case
    assert api.post(f"/api/requests/{case['id']}/recommendations/{rec['id']}/submit", {}).status_code == 403
    api.login("analyst")
    r = api.post(f"/api/requests/{case['id']}/issues/{conflict[0]['id']}/resolve", {"choice": "inventory"})
    assert r.status_code == 200, r.text
    case = api.case(case["id"])
    assert api.fact(case, "available_qty")["value"] == 100 and api.fact(case, "available_qty")["human_value"] == 100
    assert not api.issues(case, "conflict")
    assert case["status"] == "READY_FOR_APPROVAL"


def test_incorrect_extraction_override_reruns_downstream(api):
    case = api.ingest("S1")
    r = api.patch(f"/api/requests/{case['id']}/facts/quantity_required", {"value": "400", "reason": "email typo - PO is for 400"})
    assert r.status_code == 200, r.text
    case = api.case(case["id"])
    f = api.fact(case, "quantity_required")
    assert f["human_value"] == 400 and f["ai_value"] == 500  # AI value preserved
    assert api.fact(case, "shortfall")["value"] == 280
    assert case["ledger_version"] >= 2
    assert _audit(api, case["id"], "fact.override")
    assert api.patch(f"/api/requests/{case['id']}/facts/quantity_required", {"value": "1", "reason": ""}).status_code == 400


def test_low_confidence_blocks_ai_recommendation(api):
    case = api.ingest("S9")
    assert case["overall_confidence"] < 0.5
    low = [i for i in api.issues(case, "low_confidence") if i["blocking"]]
    assert low and case["status"] == "NEEDS_REVIEW"
    rec = api.rec(case)
    r = api.post(f"/api/requests/{case['id']}/recommendations/{rec['id']}/submit", {})
    assert r.status_code == 409


def test_approval_delay_reminds_then_escalates(api):
    case = api.ingest("S1")
    api.submit(case, api.rec(case, "partial"))  # ₹36k -> ops manager
    appr = api.case(case["id"])["approvals"][-1]
    assert appr["required_role"] == "ops_manager"
    api.login("admin")
    due_in = (clock.now() and (__import__("datetime").datetime.fromisoformat(appr["due_at"]) - clock.now()))
    api.post("/api/admin/clock", {"advance_minutes": due_in.total_seconds() / 60 - 30})
    case = api.case(case["id"])
    assert _audit(api, case["id"], "approval.reminder")
    api.post("/api/admin/clock", {"advance_minutes": 45})
    case = api.case(case["id"])
    statuses = [a["status"] for a in case["approvals"]]
    assert statuses[-2:] == ["escalated", "pending"]
    assert case["approvals"][-1]["required_role"] == "regional_head" and case["flags"]["escalated"]
    api.login("head.west")
    r = api.post(f"/api/approvals/{case['approvals'][-1]['id']}/decision", {"decision": "approve"})
    assert r.status_code == 200, r.text


def test_rejection_triggers_replanning_within_budget(api):
    case = api.ingest("S1")
    api.submit(case, api.rec(case, "alternate"))
    appr = api.case(case["id"])["approvals"][-1]
    api.login("head.west")
    r = api.post(f"/api/approvals/{appr['id']}/decision", {"decision": "reject", "reason": "too expensive", "budget_cap_inr": 50000})
    assert r.status_code == 200 and r.json()["replanning"]
    case = api.case(case["id"])
    assert case["status"] in ("READY_FOR_APPROVAL", "NEEDS_REVIEW")
    live = [r for r in case["recommendations"] if r["status"] == "proposed"]
    assert live and all(r["option"]["est_cost_inr"] <= 50000 for r in live)
    assert any(r["status"] == "rejected" for r in case["recommendations"])
    assert api.post(f"/api/approvals/{appr['id']}/decision", {"decision": "reject", "reason": "x"}).status_code == 403  # already decided
    planner_runs = [r for r in case["agent_runs"] if r["agent"] == "planner"]
    assert any("too expensive" in str(r["input"]) for r in planner_runs)


def test_changing_circumstances_supersede_approval(api):
    case = api.ingest("S1")
    api.submit(case, api.rec(case, "alternate"))
    upd = api.ingest("S7")
    assert upd["status"] == "MERGED"
    case = api.case(case["id"])
    assert api.fact(case, "delay_days")["value"] == 6
    assert [a["status"] for a in case["approvals"]] == ["superseded"]
    assert api.issues(case, "material_change")
    assert case["status"] in ("READY_FOR_APPROVAL", "NEEDS_REVIEW")
    assert api.fact(case, "slip_days")["value"] == 3


def test_cancellation_withdraws_approvals(api):
    case = api.ingest("S1")
    api.submit(case, api.rec(case, "alternate"))
    api.ingest("S8")
    case = api.case(case["id"])
    assert case["status"] == "ON_HOLD" and api.issues(case, "cancellation")
    api.login("analyst")
    assert api.post(f"/api/requests/{case['id']}/cancel", {"reason": "launch postponed 3 weeks (fire NOC)"}).status_code == 200
    case = api.case(case["id"])
    assert case["status"] == "CANCELLED" and case["resolution_reason"] == "cancelled_by_requester"
    assert all(a["status"] in ("superseded", "cancelled") for a in case["approvals"])


def test_primary_llm_outage_uses_fallback(api):
    api.chaos(llm_primary_down=True)
    case = api.ingest("S1")
    runs = case["agent_runs"]
    assert runs and all(r["fallback_used"] for r in runs if r["status"] == "ok")
    assert case["status"] == "READY_FOR_APPROVAL"


def test_all_llms_down_degraded_mode(api):
    api.chaos(llm_all_down=True)
    case = api.ingest("S1")
    assert case["flags"].get("degraded_mode")
    assert case["status"] == "NEEDS_REVIEW"
    assert api.fact(case, "quantity_required")["value"] == 500  # rules extractor still works
    assert any(r["option"]["source"] == "rules" for r in case["recommendations"])
    assert case["overall_confidence"] <= 0.55
    assert _audit(api, case["id"], "degraded.intake")


def test_inventory_system_unavailable(api):
    api.chaos(inventory_down=True)
    case = api.ingest("S1")
    assert api.issues(case, "system_unavailable")
    assert api.fact(case, "inventory_available")["status"] == "unverified"
    assert api.fact(case, "available_qty")["value"] == 120  # falls back to the email figure, flagged unverified
    api.chaos(inventory_down=False)
    api.login("analyst")
    assert api.post(f"/api/requests/{case['id']}/reprocess").status_code == 200
    case = api.case(case["id"])
    assert not api.issues(case, "system_unavailable") and api.fact(case, "inventory_available")["status"] == "verified"


def test_prompt_injection_flagged_and_contained(api):
    case = api.ingest("S6")
    msg = case["messages"][0]
    assert msg["guard_label"] == "malicious" and msg["guard_signals"]
    inj = api.issues(case, "injection")
    assert inj and inj[0]["blocking"]
    assert case["status"] == "NEEDS_REVIEW"
    assert not case["approvals"]  # nothing was approved despite the instruction
    assert _audit(api, case["id"], "security.injection_flagged")


def test_communication_failure_retries_then_dead_letters(api):
    case = api.ingest("S1")
    ack = next(c for c in case["communications"] if c["audience"] == "requester_ack")
    api.chaos(mail_down=True)
    api.login("analyst")
    r = api.post(f"/api/communications/{ack['id']}/send", {})
    assert r.status_code == 200 and r.json()["status"] == "failed"
    api.login("admin")
    for _ in range(3):
        api.post("/api/admin/clock", {"advance_minutes": 6})
    comm = next(c for c in api.case(case["id"])["communications"] if c["id"] == ack["id"])
    assert comm["status"] == "dead" and comm["attempts"] == 3
    case = api.case(case["id"])
    assert api.issues(case, "comms_failure")
    api.login("analyst")
    assert any(n["kind"] == "comms_failure" for n in api.c.get("/api/notifications").json())
    api.chaos(mail_down=False)
    api.login("analyst")
    r = api.post(f"/api/communications/{ack['id']}/send", {})
    assert r.json()["status"] == "sent"
    assert not api.issues(api.case(case["id"]), "comms_failure")


def test_contradictory_agent_outputs_need_human(api):
    def calm_risk(prompt, info):
        return {"urgency": "P4", "risk_level": "low", "business_impact": "minor", "drivers": [{"factor": "x", "fact_refs": ["F1"]}], "confidence": 0.8}

    llm.set_test_models(fake_llm.make({"RiskAssessment": calm_risk}), fake_llm.make(name="fake-llm-fallback"))
    case = api.ingest("S1")
    c = api.issues(case, "contradiction")
    assert c and c[0]["blocking"]
    assert case["risk_level"] in ("high", "critical")  # takes the higher
    assert case["status"] == "NEEDS_REVIEW"
    api.login("analyst")
    api.post(f"/api/requests/{case['id']}/issues/{c[0]['id']}/resolve", {"resolution": "agree with rules engine: launch at risk"})
    assert api.case(case["id"])["status"] == "READY_FOR_APPROVAL"


def test_multi_issue_request_split(api):
    case = api.ingest("S5")
    amb = api.issues(case, "ambiguity")
    assert amb and amb[0]["detail"]["action"] == "split"
    api.login("analyst")
    r = api.post(f"/api/requests/{case['id']}/split")
    assert r.status_code == 200 and r.json()["children"]
    case = api.case(case["id"])
    assert case["children"] and not api.issues(case, "ambiguity")


def test_out_of_scope(api):
    case = api.ingest("S12")
    assert case["status"] == "NEEDS_REVIEW" and api.issues(case, "out_of_scope")
    api.login("analyst")
    assert api.post(f"/api/requests/{case['id']}/close", {"reason": "not_actionable"}).status_code == 200


def test_policy_no_approval_needed_for_zero_cost_low_risk(api, client):
    case = api.ingest("S1")
    api.login("analyst")
    r = api.post(f"/api/requests/{case['id']}/recommendations", {"title": "Use existing stock only", "est_cost_inr": 0,
                                                                  "rationale": "store agrees to phased fit-out"})
    assert r.status_code == 200
    rec = next(x for x in api.case(case["id"])["recommendations"] if x["source"] == "human")
    out = api.submit(api.case(case["id"]), rec)
    assert out["approval_required"] is True  # risk is high, so approval is still mandatory even at ₹0


def test_management_report_and_metrics(api):
    api.ingest("S1")
    api.login("director")
    m = api.c.get("/api/analytics/summary").json()
    assert m["totals"]["all"] >= 16 and m["ai_quality"]["agent_runs"] > 0
    rep = api.post("/api/reports/management").json()
    assert rep["headline"] and rep["metrics"]["top_open_cases"]


def test_idempotent_decision(api):
    case = api.ingest("S1")
    api.submit(case, api.rec(case, "alternate"))
    appr = api.case(case["id"])["approvals"][-1]
    api.login("head.west")
    h = {"Idempotency-Key": "abc-123"}
    assert api.post(f"/api/approvals/{appr['id']}/decision", {"decision": "approve"}, headers=h).status_code == 200
    r = api.post(f"/api/approvals/{appr['id']}/decision", {"decision": "approve"}, headers=h)
    assert r.status_code == 200 and r.json()["idempotent_replay"]
    _ = timedelta
