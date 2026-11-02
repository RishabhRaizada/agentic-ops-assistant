"""Seeding: master data + a history of closed cases (so dashboards have something to show) +
default settings. `reset_and_seed` is the admin 'Reset demo' action."""

import random
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.clock import now
from app.db import SessionLocal, reset_db
from app.models import Approval, Case, CaseMessage, Recommendation, User
from app.seed.masterdata import seed_master
from app.services import audit

HISTORY = [
    # title, category, region, store, risk, priority, cost, resolution
    ("LED kits short for Mumbai Andheri refit", "inventory_shortage", "WEST", "MUM-AND", "medium", "P3", 24_000, "resolved_as_planned"),
    ("Delayed POS terminals for Delhi Saket", "delivery_delay", "NORTH", "DEL-SAK", "high", "P2", 68_000, "resolved_as_planned"),
    ("Damaged mannequins - Bengaluru", "quality_issue", "SOUTH", "BLR-IND", "low", "P4", 0, "resolved_alternative"),
    ("Signage vendor missed install date - Noida", "supplier_issue", "NORTH", "NOI-S18", "medium", "P3", 15_000, "resolved_as_planned"),
    ("Corporate order: 60 TVs for Infra Ltd", "customer_commitment", "NORTH", "DEL-SAK", "high", "P2", 1_20_000, "resolved_as_planned"),
    ("Trolley shortage for weekend sale - Hyderabad", "inventory_shortage", "SOUTH", "HYD-HIT", "medium", "P3", 9_000, "resolved_as_planned"),
    ("AC units delayed for Pune DC office", "delivery_delay", "WEST", None, "low", "P4", 0, "no_action_needed"),
    ("Display racks transfer approval - Mumbai to Pune", "approval_request", "WEST", "MUM-AND", "medium", "P3", 32_000, "resolved_as_planned"),
    ("Smart TV PO short-shipped by Elexa", "supplier_issue", "NORTH", "DEL-SAK", "high", "P2", 2_10_000, "resolved_alternative"),
    ("Festive gondola units for Bengaluru", "inventory_shortage", "SOUTH", "BLR-IND", "medium", "P3", 41_000, "resolved_as_planned"),
    ("Duplicate: LED kits short (via phone note)", "inventory_shortage", "WEST", "MUM-AND", "low", "P4", 0, "duplicate"),
    ("Customer pickup delay - Noida", "customer_commitment", "NORTH", "NOI-S18", "medium", "P3", 5_000, "resolved_as_planned"),
    ("Vendor bank change request (rejected - suspected fraud)", "supplier_issue", "WEST", None, "high", "P2", 0, "not_actionable"),
    ("Shelf lighting for Hyderabad soft launch", "delivery_delay", "SOUTH", "HYD-HIT", "medium", "P3", 18_500, "resolved_as_planned"),
    ("Cancelled: pop-up store fixtures", "delivery_delay", "WEST", "PUN-HJW", "medium", "P3", 0, "cancelled_by_requester"),
]


def seed_history(db: Session) -> None:
    rnd = random.Random(42)
    users = {u.role + ":" + u.region: u for u in db.scalars(select(User))}
    analyst = users["analyst:ALL"]
    for i, (title, cat, region, store, risk, prio, cost, resolution) in enumerate(HISTORY, start=1):
        created = now() - timedelta(days=rnd.randint(2, 13), hours=rnd.randint(0, 8))
        triaged = created + timedelta(minutes=rnd.randint(1, 4))
        decided = triaged + timedelta(hours=rnd.uniform(0.5, 6))
        closed = decided + timedelta(hours=rnd.uniform(4, 40))
        status = "CANCELLED" if resolution == "cancelled_by_requester" else "CLOSED"
        c = Case(ref=f"OPS-H{i:03d}", title=title, summary=f"Historical request: {title}.", category=cat, status=status,
                 priority=prio, risk_level=risk, region=region, store_code=store, overall_confidence=round(rnd.uniform(0.62, 0.93), 2),
                 created_at=created, triaged_at=triaged, approved_at=decided if cost else None, closed_at=closed if status == "CLOSED" else None,
                 cancelled_at=closed if status == "CANCELLED" else None, resolution_reason=resolution, created_by_id=analyst.id,
                 assignee_id=analyst.id, flags={}, analysis={"historical": True})
        db.add(c)
        db.flush()
        db.add(CaseMessage(case_id=c.id, channel=rnd.choice(["email", "email", "manual"]), from_addr="ops.requests@retail.demo",
                           from_name="Ops requester", subject=title, body=f"(historical record) {title}", received_at=created))
        if cost:
            role = "ops_manager" if cost <= 50_000 else "regional_head" if cost <= 500_000 else "director"
            approver = users.get(f"{role}:{region}") or users["director:ALL"]
            rec = Recommendation(case_id=c.id, option_key="A", option={"key": "A", "title": f"Resolve: {title}", "est_cost_inr": cost, "steps": []},
                                 is_recommended=rnd.random() > 0.2, status="approved", plan_hash=f"hist-{i}")
            db.add(rec)
            db.flush()
            due = triaged + timedelta(hours=4)
            db.add(Approval(case_id=c.id, recommendation_id=rec.id, plan_hash=rec.plan_hash, ledger_version=1, amount_inr=cost,
                            required_role=role, region=region, assigned_to_id=approver.id, requested_by_id=analyst.id, due_at=due,
                            status="approved", decided_by_id=approver.id, decided_at=decided, created_at=triaged))
        audit.record(db, action="history.imported", summary=f"Historical case {c.ref} imported ({resolution})", actor_type="system",
                     actor_id="seed", case_id=c.id)


def seed(db: Session) -> None:
    seed_master(db)
    audit.record(db, action="system.seeded", summary="Demo master data seeded (stores, SKUs, inventory, suppliers, POs, quotes, users)",
                 actor_type="system", actor_id="seed", pinned=True)
    seed_history(db)


def reset_and_seed(actor: str = "system") -> None:
    reset_db()
    with SessionLocal() as db:
        audit.record(db, action="system.demo_reset", summary=f"Demo data reset by {actor}", actor_type="human" if "@" in actor else "system",
                     actor_id=actor, pinned=True)
        seed(db)
        db.commit()


if __name__ == "__main__":
    reset_and_seed()
    print("seeded")
