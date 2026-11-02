"""Tamper-evident audit trail. Each event hashes its own content plus the previous event's hash,
so editing or deleting any historical row breaks verification from that row onward."""

import hashlib
import json
import threading
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.clock import now
from app.models import AuditEvent, User

GENESIS = "0" * 64
_chain_lock = threading.Lock()


def _digest(prev_hash: str, payload: dict[str, Any]) -> str:
    blob = json.dumps(payload, sort_keys=True, default=str, separators=(",", ":"))
    return hashlib.sha256((prev_hash + blob).encode()).hexdigest()


def _payload(ev: AuditEvent) -> dict[str, Any]:
    return {
        "ts": ev.ts.isoformat(),
        "case_id": ev.case_id,
        "actor_type": ev.actor_type,
        "actor_id": ev.actor_id,
        "action": ev.action,
        "entity": ev.entity,
        "entity_id": ev.entity_id,
        "summary": ev.summary,
        "before": ev.before,
        "after": ev.after,
        "meta": ev.meta,
    }


def actor_of(user: User | None) -> tuple[str, str]:
    return ("human", user.email) if user else ("system", "system")


def record(
    db: Session,
    *,
    action: str,
    summary: str,
    actor_type: str,
    actor_id: str,
    case_id: int | None = None,
    entity: str = "case",
    entity_id: str | int | None = None,
    before: Any = None,
    after: Any = None,
    meta: dict[str, Any] | None = None,
    pinned: bool = False,
) -> AuditEvent:
    """Append one event. Flushes immediately so the chain head is visible to the next writer."""
    with _chain_lock:
        last = db.scalar(select(AuditEvent).order_by(AuditEvent.id.desc()).limit(1))
        prev = last.hash if last else GENESIS
        ev = AuditEvent(
            ts=now(),
            case_id=case_id,
            actor_type=actor_type,
            actor_id=actor_id,
            action=action,
            entity=entity,
            entity_id=str(entity_id) if entity_id is not None else None,
            summary=summary,
            before=before,
            after=after,
            meta=meta or {},
            pinned=pinned,
            prev_hash=prev,
            hash="",
        )
        # Normalise JSON round-trip (e.g. tuples -> lists) before hashing so verify() matches.
        ev.before = json.loads(json.dumps(before, default=str)) if before is not None else None
        ev.after = json.loads(json.dumps(after, default=str)) if after is not None else None
        ev.meta = json.loads(json.dumps(meta or {}, default=str))
        ev.hash = _digest(prev, _payload(ev))
        db.add(ev)
        db.flush()
        return ev


def verify(db: Session) -> dict[str, Any]:
    prev = GENESIS
    count = 0
    for ev in db.scalars(select(AuditEvent).order_by(AuditEvent.id)):
        count += 1
        if ev.prev_hash != prev or _digest(prev, _payload(ev)) != ev.hash:
            return {"ok": False, "checked": count, "broken_at_id": ev.id}
        prev = ev.hash
    return {"ok": True, "checked": count, "head": prev}
