"""Adapters for the enterprise systems an ops team would query: inventory, purchase orders, supplier
quotes, transfer lanes and the store launch calendar. Backed by seeded tables in the prototype;
each call goes through a circuit breaker and honours the admin chaos flags."""

import re
from typing import Any

from rapidfuzz import fuzz, process
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.integrations.breaker import BREAKERS, CircuitOpen
from app.models import InventoryLevel, PurchaseOrder, Sku, Store, Supplier, SupplierQuote
from app.services import runtime_settings


class SystemUnavailable(Exception):
    def __init__(self, system: str, detail: str):
        super().__init__(f"{system} unavailable: {detail}")
        self.system = system
        self.detail = detail


def _guard_inventory(db: Session) -> None:
    br = BREAKERS["inventory"]
    try:
        br.before()
    except CircuitOpen as e:
        raise SystemUnavailable("Inventory system", str(e)) from e
    if runtime_settings.chaos(db)["inventory_down"]:
        br.failure()
        raise SystemUnavailable("Inventory system", "HTTP 503 from inventory API (chaos flag)")
    br.success()


# ------------------------------------------------------------------ master-data resolution


def resolve_store(db: Session, text: str | None) -> Store | None:
    if not text:
        return None
    stores = list(db.scalars(select(Store).where(Store.kind == "store")))
    choices = {s.code: f"{s.name} {s.city} {s.code}" for s in stores}
    t = text.lower()
    # A city match is strong evidence; prefer a launching store in that city.
    city_hits = [s for s in stores if s.city.lower() in t]
    if city_hits:
        city_hits.sort(key=lambda s: (s.status != "launching", s.code))
        return city_hits[0]
    best = process.extractOne(text, choices, scorer=fuzz.token_set_ratio)
    if best and best[1] >= 70:
        return next(s for s in stores if s.code == best[2])
    return None


GENERIC = {"units", "unit", "pcs", "pieces", "nos", "items", "item", "stock", "the", "of", "for", "qty", "inch", "new", "store"}


def _content_words(text: str) -> str:
    return " ".join(w for w in re.findall(r"[a-z0-9]+", text.lower()) if w not in GENERIC)


def resolve_sku(db: Session, text: str | None) -> Sku | None:
    """Map free text ('500 display units', '55" Smart TV') to a SKU. Generic words are ignored so
    'units' alone never matches 'display units'."""
    if not text:
        return None
    skus = list(db.scalars(select(Sku)))
    t = text.lower()
    for s in skus:
        if s.code.lower() in t:
            return s
    words = _content_words(text)
    if not words:
        return None
    best: tuple[float, Sku | None] = (0, None)
    for s in skus:
        for name in [s.name, *s.aliases]:
            cand = _content_words(name)
            if not cand:
                continue
            score = fuzz.token_set_ratio(words, cand) if len(words.split()) > 1 or len(cand.split()) == 1 else fuzz.ratio(words, cand)
            if score > best[0]:
                best = (score, s)
    return best[1] if best[0] >= 80 else None


def resolve_supplier(db: Session, text: str | None) -> Supplier | None:
    if not text:
        return None
    sups = list(db.scalars(select(Supplier)))
    best = process.extractOne(text, {s.code: s.name for s in sups}, scorer=fuzz.token_set_ratio)
    if best and best[1] >= 75:
        return next(s for s in sups if s.code == best[2])
    return None


def store_by_code(db: Session, code: str | None) -> Store | None:
    return db.scalar(select(Store).where(Store.code == code)) if code else None


# ------------------------------------------------------------------ inventory system


def get_inventory(db: Session, sku_code: str, location_code: str | None = None) -> list[dict[str, Any]]:
    _guard_inventory(db)
    q = select(InventoryLevel).where(InventoryLevel.sku_code == sku_code)
    if location_code:
        q = q.where(InventoryLevel.location_code == location_code)
    return [
        {
            "location": r.location_code,
            "on_hand": r.on_hand,
            "reserved": r.reserved,
            "available": r.on_hand - r.reserved,
            "transferable": r.transferable,
            "updated_at": r.updated_at.isoformat(),
        }
        for r in db.scalars(q)
    ]


def get_transfer_options(db: Session, sku_code: str, to_location: str) -> list[dict[str, Any]]:
    _guard_inventory(db)
    out = []
    lanes = db.scalars(
        select(SupplierQuote).where(
            SupplierQuote.kind == "transfer",
            SupplierQuote.sku_code == sku_code,
            SupplierQuote.to_location == to_location,
        )
    )
    for lane in lanes:
        inv = db.scalar(
            select(InventoryLevel).where(
                InventoryLevel.sku_code == sku_code, InventoryLevel.location_code == lane.from_location
            )
        )
        qty = min(lane.max_quantity, inv.transferable if inv else 0)
        if qty <= 0:
            continue
        out.append(
            {
                "from_location": lane.from_location,
                "to_location": to_location,
                "max_quantity": qty,
                "lead_days": lane.lead_days,
                "fixed_cost_inr": lane.fixed_cost_inr,
                "unit_premium_inr": lane.unit_premium_inr,
                "notes": lane.notes,
            }
        )
    return out


# ------------------------------------------------------------------ procurement / supplier portal


def get_purchase_orders(db: Session, sku_code: str, dest_location: str | None = None) -> list[dict[str, Any]]:
    q = select(PurchaseOrder).where(PurchaseOrder.sku_code == sku_code)
    if dest_location:
        q = q.where(PurchaseOrder.dest_location == dest_location)
    return [
        {
            "po_number": p.po_number,
            "supplier_code": p.supplier_code,
            "quantity": p.quantity,
            "dest_location": p.dest_location,
            "promised_date": p.promised_date.date().isoformat(),
            "revised_date": p.revised_date.date().isoformat() if p.revised_date else None,
            "status": p.status,
        }
        for p in db.scalars(q)
    ]


def list_alternate_suppliers(db: Session, sku_code: str) -> list[dict[str, Any]]:
    rows = db.scalars(
        select(SupplierQuote).where(SupplierQuote.kind == "alternate_supply", SupplierQuote.sku_code == sku_code)
    )
    out = []
    for q in rows:
        sup = db.scalar(select(Supplier).where(Supplier.code == q.supplier_code))
        out.append(
            {
                "supplier_code": q.supplier_code,
                "supplier_name": sup.name if sup else q.supplier_code,
                "reliability": sup.reliability if sup else None,
                "is_contracted": sup.is_contracted if sup else False,
                "max_quantity": q.max_quantity,
                "lead_days": q.lead_days,
                "unit_premium_inr": q.unit_premium_inr,
                "fixed_cost_inr": q.fixed_cost_inr,
                "notes": q.notes,
            }
        )
    return out


def get_expedite_quotes(db: Session, sku_code: str) -> list[dict[str, Any]]:
    rows = db.scalars(select(SupplierQuote).where(SupplierQuote.kind == "expedite", SupplierQuote.sku_code == sku_code))
    return [
        {
            "supplier_code": q.supplier_code,
            "max_quantity": q.max_quantity,
            "lead_days": q.lead_days,
            "unit_premium_inr": q.unit_premium_inr,
            "fixed_cost_inr": q.fixed_cost_inr,
            "notes": q.notes,
        }
        for q in rows
    ]


# ------------------------------------------------------------------ store calendar


def get_launch_calendar(db: Session, store_code: str) -> dict[str, Any] | None:
    s = store_by_code(db, store_code)
    if not s:
        return None
    return {
        "store_code": s.code,
        "store_name": s.name,
        "status": s.status,
        "launch_date": s.launch_date.date().isoformat() if s.launch_date else None,
        "expected_daily_revenue_inr": s.expected_daily_revenue_inr,
        "serving_dc": s.serving_dc,
        "manager": s.manager_name,
    }
