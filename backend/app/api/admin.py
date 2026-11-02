"""Admin controls (approval matrix, thresholds, chaos/fault injection, demo clock, reset), the
simulated inbox of scenario emails, and health/readiness probes."""

import hashlib
import hmac
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app import clock, worker
from app.config import get_settings
from app.db import get_db
from app.integrations import llm
from app.integrations.breaker import BREAKERS
from app.models import User
from app.orchestrator import pipeline
from app.security import current_user, require_admin
from app.services import audit, runtime_settings

router = APIRouter(prefix="/api/admin", tags=["admin"])
public = APIRouter(tags=["health"])


@router.get("/settings")
def get_all(user: User = Depends(current_user), db: Session = Depends(get_db)):
    return {k: runtime_settings.get(db, k) for k in runtime_settings.DEFAULTS} | {
        "clock_offset_seconds": clock.offset_seconds(), "now": clock.now().isoformat(), "local_now": clock.local_now().isoformat(),
        "llm": {"configured": get_settings().llm_enabled, "primary": llm.model_names()[0], "fallback": llm.model_names()[1],
                "chain": list(dict.fromkeys(llm.chain_names()))},
    }


class SettingIn(BaseModel):
    value: Any


@router.put("/settings/{key}")
def put_setting(key: str, body: SettingIn, user: User = Depends(require_admin), db: Session = Depends(get_db)):
    if key not in runtime_settings.DEFAULTS:
        raise HTTPException(404, "Unknown setting")
    before = runtime_settings.get(db, key)
    value = body.value
    if key == "chaos":
        value = {**runtime_settings.chaos(db), **(value or {})}
        if not value.get("inventory_down"):
            BREAKERS["inventory"].reset()
        if not value.get("mail_down"):
            BREAKERS["mail"].reset()
    runtime_settings.put(db, key, value)
    audit.record(db, action=f"admin.{key}", summary=f"{user.name} changed {key}", actor_type="human", actor_id=user.email,
                 entity="setting", entity_id=key, before=before, after=value, pinned=True)
    db.commit()
    return {"key": key, "value": value}


class ClockIn(BaseModel):
    advance_minutes: float | None = None
    reset: bool = False


@router.post("/clock")
def demo_clock(body: ClockIn, user: User = Depends(require_admin), db: Session = Depends(get_db)):
    before = clock.offset_seconds()
    clock.set_offset(0 if body.reset else before + (body.advance_minutes or 0) * 60)
    runtime_settings.put(db, "clock_offset_seconds", clock.offset_seconds())
    audit.record(db, action="admin.clock", summary=f"Demo clock {'reset' if body.reset else f'advanced {body.advance_minutes:g} min'} (offset now {clock.offset_seconds() / 3600:.2f} h)",
                 actor_type="human", actor_id=user.email, entity="setting", entity_id="clock", pinned=True)
    db.commit()
    n = worker.sweep_once()  # run the SLA monitor immediately so the effect is visible
    return {"offset_seconds": clock.offset_seconds(), "now": clock.now().isoformat(), "sla_actions": n}


@router.post("/reset-demo")
def reset_demo(user: User = Depends(require_admin)):
    from app.seed.run import reset_and_seed

    clock.set_offset(0)
    reset_and_seed(actor=user.email)
    return {"ok": True}


@router.post("/sweep")
def sweep(user: User = Depends(require_admin)):
    return {"actions": worker.sweep_once()}


# ----------------------------------------------------------------------------- simulated inbox


@router.get("/scenarios")
def scenarios(user: User = Depends(current_user)):
    from app.seed.scenarios import render_all

    return render_all()


class ScenarioIn(BaseModel):
    key: str


@router.post("/scenarios/ingest", status_code=202)
def ingest_scenario(body: ScenarioIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    from app.seed.scenarios import render_all

    sc = next((s for s in render_all() if s["key"] == body.key), None)
    if not sc:
        raise HTTPException(404, "Unknown scenario")
    if user.role not in ("analyst", "ops_manager", "regional_head", "director", "admin"):
        raise HTTPException(403, "Read-only role")
    case = pipeline.ingest(db, channel="email", from_addr=sc["from_addr"], from_name=sc["from_name"], subject=sc["subject"],
                           body=sc["body"], user=None if user.role == "admin" else user)
    if user.role == "analyst" and not case.assignee_id:
        case.assignee_id = user.id
    elif not case.assignee_id:
        analyst = db.scalar(select(User).where(User.role == "analyst"))
        case.assignee_id = analyst.id if analyst else None
    db.commit()
    return {"id": case.id, "ref": case.ref}


# ----------------------------------------------------------------------------- inbound webhook


class InboundIn(BaseModel):
    from_addr: str
    from_name: str = ""
    subject: str
    body: str


@public.post("/api/intake/webhook", status_code=202)
async def inbound_webhook(request: Request, db: Session = Depends(get_db)):
    """Email-provider webhook (e.g. SendGrid/Postmark inbound parse). Body must be signed with
    HMAC-SHA256 using INTAKE_WEBHOOK_SECRET in the X-Signature header."""
    secret = get_settings().intake_webhook_secret
    if not secret:
        raise HTTPException(503, "Webhook intake disabled")
    raw = await request.body()
    sig = request.headers.get("x-signature", "")
    if not hmac.compare_digest(sig, hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()):
        raise HTTPException(401, "Bad signature")
    data = InboundIn.model_validate_json(raw)
    case = pipeline.ingest(db, channel="webhook", from_addr=data.from_addr, from_name=data.from_name, subject=data.subject,
                           body=data.body, user=None)
    analyst = db.scalar(select(User).where(User.role == "analyst"))
    case.assignee_id = analyst.id if analyst else None
    db.commit()
    return {"id": case.id, "ref": case.ref}


# ----------------------------------------------------------------------------- health


@public.get("/api/healthz")
def healthz():
    return {"ok": True}


@public.get("/api/readyz")
def readyz(db: Session = Depends(get_db)):
    db.execute(text("SELECT 1"))
    chaos = runtime_settings.chaos(db)
    return {
        "db": "ok",
        "llm": {"configured": get_settings().llm_enabled, "breaker": BREAKERS["llm"].state,
                "chaos": "all down" if chaos["llm_all_down"] else "primary down" if chaos["llm_primary_down"] else None},
        "inventory": {"breaker": BREAKERS["inventory"].state, "chaos_down": chaos["inventory_down"]},
        "mail": {"provider": get_settings().mail_provider, "breaker": BREAKERS["mail"].state, "chaos_down": chaos["mail_down"]},
        "sla_monitor": worker.last_sweep,
        "audit_chain": audit.verify(db)["ok"],
    }
