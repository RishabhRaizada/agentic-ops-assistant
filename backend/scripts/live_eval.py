"""Run simulated-inbox scenarios through the real pipeline against the configured LLM and print
what the agents produced. Usage (from backend/): python -m scripts.live_eval S1 S4 S6 ..."""

import json
import os
import sys
import tempfile
import time

os.environ.setdefault("RUN_BACKGROUND_WORKERS", "false")
os.environ["DATABASE_URL"] = f"sqlite:///{tempfile.mkdtemp()}/live.db"
os.environ.setdefault("PYDANTIC_AI_NO_BANNER", "1")

from app import db  # noqa: E402
from app.models import Case  # noqa: E402
from app.orchestrator import pipeline  # noqa: E402
from app.seed.run import seed  # noqa: E402
from app.seed.scenarios import render_all  # noqa: E402

db.init_db()
with db.SessionLocal() as s:
    seed(s)
    s.commit()
pipeline.SYNC.inline = True
keys = sys.argv[1:] or ["S1"]
scen = {x["key"]: x for x in render_all()}
for k in keys:
    sc = scen[k]
    t = time.time()
    with db.SessionLocal() as s:
        c = pipeline.ingest(s, channel="email", from_addr=sc["from_addr"], from_name=sc["from_name"], subject=sc["subject"], body=sc["body"], user=None)
        cid = c.id
    with db.SessionLocal() as s:
        c = s.get(Case, cid)
        print(f"\n=== {k} {sc['tag']} -> {c.ref} {c.status} risk={c.risk_level}/{c.priority} conf={c.overall_confidence} ({time.time() - t:.1f}s)")
        print("   title:", c.title, "| category:", c.category, "| clarity:", c.intent_clarity, "| flags:", c.flags)
        for f in c.facts:
            if f.source in ("email", "update") and f.status != "missing":
                print(f"   {f.ref} {f.key} = {f.value!r} [{f.status} conf={f.confidence:.2f} grounded={f.grounded}]")
        for i in c.issues:
            print(f"   ISSUE[{i.status}{'/blocking' if i.blocking else ''}] {i.type}: {i.title}")
        from app.models import AgentRun, Recommendation
        for r in s.query(Recommendation).filter_by(case_id=cid).all():
            o = r.option
            print(f"   OPT {r.option_key}{'*' if r.is_recommended else ''} [{r.status}] {o['title']} cost={o['est_cost_inr']:.0f} covers={o['covers_qty']} eta={o['eta_days']} tools={o.get('tool_refs')}")
        for r in s.query(AgentRun).filter_by(case_id=cid).all():
            print(f"   RUN {r.agent}:{r.step} {r.status} {r.model} fb={r.fallback_used} {r.latency_ms}ms retries={r.validation_retries} tools={len(r.tool_calls)} {('ERR ' + r.error[:160]) if r.error else ''}")
        if c.analysis.get("plan"):
            print("   plan:", json.dumps({k2: c.analysis["plan"].get(k2) for k2 in ("rationale", "revised", "review")}, default=str)[:600])
