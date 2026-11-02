"""Master data for the demo retail chain. All dates are relative to 'now' at seed time so the
scenario ('store opens next week') stays true whenever the demo is reset."""

from datetime import timedelta

from sqlalchemy.orm import Session

from app.clock import local_now
from app.models import InventoryLevel, PurchaseOrder, Sku, Store, Supplier, SupplierQuote, User
from app.security import hash_password

DEMO_PASSWORD = "Demo@2026"

USERS = [
    # email, name, title, role, region, limit
    ("analyst@opspilot.demo", "Ananya Iyer", "Operations Analyst, Central Ops Desk", "analyst", "ALL", 0),
    ("manager.west@opspilot.demo", "Rohan Mehta", "Operations Manager, West", "ops_manager", "WEST", 50_000),
    ("head.west@opspilot.demo", "Kavita Rao", "Regional Operations Head, West", "regional_head", "WEST", 500_000),
    ("manager.north@opspilot.demo", "Vikram Singh", "Operations Manager, North", "ops_manager", "NORTH", 50_000),
    ("head.north@opspilot.demo", "Neha Kapoor", "Regional Operations Head, North", "regional_head", "NORTH", 500_000),
    ("manager.south@opspilot.demo", "Suresh Pillai", "Operations Manager, South", "ops_manager", "SOUTH", 50_000),
    ("head.south@opspilot.demo", "Divya Menon", "Regional Operations Head, South", "regional_head", "SOUTH", 500_000),
    ("director@opspilot.demo", "Arjun Malhotra", "Director, Supply Chain & Finance", "director", "ALL", 10_000_000),
    ("auditor@opspilot.demo", "Meera Nair", "Internal Audit", "auditor", "ALL", 0),
    ("admin@opspilot.demo", "Platform Admin", "Platform Administrator", "admin", "ALL", 0),
]


def seed_master(db: Session) -> None:
    today = local_now().replace(hour=10, minute=0, second=0, microsecond=0)
    pw = hash_password(DEMO_PASSWORD)
    for email, name, title, role, region, limit in USERS:
        db.add(User(email=email, name=name, title=title, role=role, region=region, approval_limit_inr=limit, password_hash=pw))

    stores = [
        Store(code="PUN-HJW", name="Pune Hinjewadi (new flagship)", city="Pune", region="WEST", status="launching",
              serving_dc="PUN-DC", launch_date=today + timedelta(days=6), expected_daily_revenue_inr=800_000,
              manager_name="Sameer Kulkarni", manager_email="sameer.kulkarni@retail.demo"),
        Store(code="MUM-AND", name="Mumbai Andheri", city="Mumbai", region="WEST", serving_dc="MUM-DC",
              expected_daily_revenue_inr=1_100_000, manager_name="Farah Shaikh", manager_email="farah.shaikh@retail.demo"),
        Store(code="BLR-IND", name="Bengaluru Indiranagar", city="Bengaluru", region="SOUTH", serving_dc="BLR-DC",
              expected_daily_revenue_inr=900_000, manager_name="Karthik Reddy", manager_email="karthik.reddy@retail.demo"),
        Store(code="HYD-HIT", name="Hyderabad HITEC City", city="Hyderabad", region="SOUTH", serving_dc="BLR-DC",
              expected_daily_revenue_inr=700_000, manager_name="Lakshmi Prasad", manager_email="lakshmi.prasad@retail.demo"),
        Store(code="DEL-SAK", name="Delhi Saket", city="Delhi", region="NORTH", serving_dc="DEL-DC",
              expected_daily_revenue_inr=1_200_000, manager_name="Amit Bansal", manager_email="amit.bansal@retail.demo"),
        Store(code="NOI-S18", name="Noida Sector 18", city="Noida", region="NORTH", serving_dc="DEL-DC",
              expected_daily_revenue_inr=650_000, manager_name="Pooja Arora", manager_email="pooja.arora@retail.demo"),
    ]
    dcs = [
        Store(code="PUN-DC", name="Pune Distribution Centre", city="Pune DC", region="WEST", kind="dc"),
        Store(code="MUM-DC", name="Mumbai Distribution Centre", city="Bhiwandi", region="WEST", kind="dc"),
        Store(code="BLR-DC", name="Bengaluru Distribution Centre", city="Hoskote", region="SOUTH", kind="dc"),
        Store(code="DEL-DC", name="Delhi Distribution Centre", city="Gurugram", region="NORTH", kind="dc"),
    ]
    db.add_all(stores + dcs)

    db.add_all([
        Sku(code="DSP-500", name="Modular Display Unit", aliases=["display units", "display unit", "display fixtures", "fixtures", "display racks", "gondola units"], unit_cost_inr=4_500),
        Sku(code="LED-210", name="LED Shelf Lighting Kit", aliases=["led kits", "shelf lights", "lighting kits"], unit_cost_inr=1_800),
        Sku(code="POS-120", name="POS Terminal", aliases=["pos terminals", "billing counters", "billing machines"], unit_cost_inr=38_000),
        Sku(code="MNQ-030", name="Mannequin Set", aliases=["mannequins"], unit_cost_inr=6_200),
        Sku(code="SGN-700", name="Store Signage Pack", aliases=["signage", "signboards"], unit_cost_inr=22_000),
        Sku(code="AC-SPL15", name="Split AC 1.5T", aliases=["air conditioners", "ac units"], unit_cost_inr=36_000),
        Sku(code="TRL-090", name="Shopping Trolley", aliases=["trolleys", "carts"], unit_cost_inr=3_100),
        Sku(code="SMT-TV55", name="Smart TV 55\" (retail SKU)", aliases=["55 inch tvs", "smart tv", "smart tvs", "televisions"], unit_cost_inr=41_000),
    ])

    inv = [
        ("DSP-500", "PUN-DC", 132, 12, 0), ("DSP-500", "MUM-AND", 210, 60, 150), ("DSP-500", "MUM-DC", 40, 0, 40),
        ("DSP-500", "BLR-DC", 75, 15, 60), ("DSP-500", "DEL-DC", 90, 10, 80),
        ("LED-210", "PUN-DC", 400, 20, 0), ("POS-120", "PUN-DC", 6, 0, 0), ("POS-120", "MUM-DC", 14, 2, 8),
        ("MNQ-030", "PUN-DC", 30, 0, 0), ("SGN-700", "PUN-DC", 2, 0, 0), ("SMT-TV55", "DEL-DC", 140, 40, 0),
        ("SMT-TV55", "MUM-DC", 60, 10, 30), ("TRL-090", "DEL-DC", 300, 0, 120),
    ]
    for sku, loc, oh, res, tr in inv:
        db.add(InventoryLevel(sku_code=sku, location_code=loc, on_hand=oh, reserved=res, transferable=tr))

    db.add_all([
        Supplier(code="VTX", name="Vertex Fixtures Pvt Ltd", contact_email="dispatch@vertexfixtures.demo", reliability=0.82),
        Supplier(code="SHR", name="Shree Display Systems", contact_email="sales@shreedisplay.demo", reliability=0.9),
        Supplier(code="NOV", name="Nova Retail Interiors", contact_email="orders@novainteriors.demo", reliability=0.75, is_contracted=False),
        Supplier(code="ELX", name="Elexa Electronics Distribution", contact_email="b2b@elexa.demo", reliability=0.93),
    ])
    db.add_all([
        PurchaseOrder(po_number="PO-2026-4471", supplier_code="VTX", sku_code="DSP-500", quantity=500, dest_location="PUN-DC",
                      promised_date=today + timedelta(days=3), status="delayed"),
        PurchaseOrder(po_number="PO-2026-4502", supplier_code="ELX", sku_code="SMT-TV55", quantity=300, dest_location="DEL-DC",
                      promised_date=today + timedelta(days=2), status="open"),
        PurchaseOrder(po_number="PO-2026-4510", supplier_code="VTX", sku_code="LED-210", quantity=200, dest_location="PUN-DC",
                      promised_date=today + timedelta(days=5), status="open"),
    ])
    db.add_all([
        # Inter-store / DC transfer lanes into the Pune launch store
        SupplierQuote(kind="transfer", sku_code="DSP-500", from_location="MUM-AND", to_location="PUN-HJW", max_quantity=150, lead_days=2,
                      fixed_cost_inr=18_000, notes="Mumbai Andheri holds surplus after its refit; road transfer"),
        SupplierQuote(kind="transfer", sku_code="DSP-500", from_location="BLR-DC", to_location="PUN-HJW", max_quantity=60, lead_days=3,
                      fixed_cost_inr=12_000, notes="Part-load truck"),
        SupplierQuote(kind="transfer", sku_code="DSP-500", from_location="MUM-DC", to_location="PUN-HJW", max_quantity=40, lead_days=1,
                      fixed_cost_inr=6_000, notes="Daily milk-run Bhiwandi to Pune"),
        SupplierQuote(kind="transfer", sku_code="SMT-TV55", from_location="MUM-DC", to_location="DEL-SAK", max_quantity=30, lead_days=3,
                      fixed_cost_inr=22_000, notes="Insured transfer"),
        # Alternate supply
        SupplierQuote(kind="alternate_supply", supplier_code="SHR", sku_code="DSP-500", max_quantity=300, lead_days=4,
                      unit_premium_inr=700, fixed_cost_inr=15_000, notes="Same spec; premium over contract price; Vertex PO reduced accordingly"),
        SupplierQuote(kind="alternate_supply", supplier_code="NOV", sku_code="DSP-500", max_quantity=500, lead_days=8,
                      unit_premium_inr=350, fixed_cost_inr=25_000, notes="Not a contracted supplier; quality audit pending"),
        # Expedite from the current supplier
        SupplierQuote(kind="expedite", supplier_code="VTX", sku_code="DSP-500", max_quantity=400, lead_days=5,
                      unit_premium_inr=550, fixed_cost_inr=80_000, notes="Air freight for a split shipment of the delayed PO"),
        SupplierQuote(kind="expedite", supplier_code="ELX", sku_code="SMT-TV55", max_quantity=150, lead_days=1,
                      unit_premium_inr=400, fixed_cost_inr=30_000, notes="Priority dispatch"),
    ])
    db.flush()
