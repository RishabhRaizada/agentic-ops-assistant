"""Exercise the human-in-the-loop flows against the configured (real) LLM through the HTTP API:
rejection -> re-plan under a budget cap, approval delay -> escalation, postponement -> cancellation."""

import datetime as dt
import os
import tempfile

os.environ["RUN_BACKGROUND_WORKERS"] = "false"
os.environ["DATABASE_URL"] = f"sqlite:///{tempfile.mkdtemp()}/flows.db"
os.environ.setdefault("PYDANTIC_AI_NO_BANNER", "1")

from fastapi.testclient import TestClient  # noqa: E402

from app import clock  # noqa: E402
from app.main import app  # noqa: E402
from app.orchestrator import pipeline  # noqa: E402

pipeline.SYNC.inline = True
H = {"x-requested-with": "opspilot"}


def run():
    with TestClient(app) as c:
        def login(who):
            assert c.post("/api/auth/login", json={"email": f"{who}@opspilot.demo", "password": "Demo@2026"}).status_code == 200

        def ingest(key):
            login("analyst")
            return c.get(f"/api/requests/{c.post('/api/admin/scenarios/ingest', json={'key': key}, headers=H).json()['id']}").json()

        def live(case):
            return [r for r in case["recommendations"] if r["status"] in ("proposed", "selected")]

        case = ingest("S1")
        print("S1:", case["status"], [(r["option_key"], r["option"]["title"], r["option"]["est_cost_inr"]) for r in live(case)])
        big = max(live(case), key=lambda r: r["option"]["est_cost_inr"])
        print(" submit:", c.post(f"/api/requests/{case['id']}/recommendations/{big['id']}/submit", json={"note": ""}, headers=H).json())
        appr = c.get(f"/api/requests/{case['id']}").json()["approvals"][-1]
        login("manager.north")
        print(" north manager:", c.post(f"/api/approvals/{appr['id']}/decision", json={"decision": "approve"}, headers=H).json())
        login(appr["assigned_to"]["email"].split("@")[0])
        r = c.post(f"/api/approvals/{appr['id']}/decision", json={"decision": "reject", "reason": "Too expensive - use internal stock first", "budget_cap_inr": 50000}, headers=H)
        print(" reject:", r.status_code, r.json())
        case = c.get(f"/api/requests/{case['id']}").json()
        print(" after replan:", case["status"], [(r["option_key"], r["option"]["title"], r["option"]["est_cost_inr"]) for r in live(case)])
        planners = [x for x in case["agent_runs"] if x["agent"] == "planner"]
        print(" planner runs:", [(x["step"], x["status"], x["model"], x["validation_retries"]) for x in planners])

        cheap = min(live(case), key=lambda r: r["option"]["est_cost_inr"])
        login("analyst")
        print(" submit cheap:", c.post(f"/api/requests/{case['id']}/recommendations/{cheap['id']}/submit", json={"note": "within cap"}, headers=H).json())
        appr = c.get(f"/api/requests/{case['id']}").json()["approvals"][-1]
        due = dt.datetime.fromisoformat(appr["due_at"])
        login("admin")
        c.post("/api/admin/clock", json={"advance_minutes": (due - clock.now()).total_seconds() / 60 + 2}, headers=H)
        case = c.get(f"/api/requests/{case['id']}").json()
        print(" escalation:", [(a["required_role"], a["status"]) for a in case["approvals"]], case["flags"])
        clock.set_offset(0)

        upd = ingest("S8")
        case = c.get(f"/api/requests/{case['id']}").json()
        print("S8:", upd["status"], "->", case["status"], [i["title"] for i in case["issues"] if i["status"] == "open" and i["type"] == "cancellation"])
        login("analyst")
        print(" cancel:", c.post(f"/api/requests/{case['id']}/cancel", json={"reason": "Launch postponed 3 weeks (fire NOC)"}, headers=H).json())
        case = c.get(f"/api/requests/{case['id']}").json()
        print(" final:", case["status"], [(a["required_role"], a["status"]) for a in case["approvals"]], [x["audience"] for x in case["communications"]])
        print(" audit chain:", c.get("/api/audit/verify").json())


run()
