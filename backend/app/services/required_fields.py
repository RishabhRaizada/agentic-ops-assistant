"""Which facts a request of each category needs before the team can act on it.
`critical` ones block planning (case goes to NEEDS_INFO); the rest only lower confidence."""

REQUIRED = {
    "delivery_delay": {"critical": ["item", "quantity_required", "location"], "important": ["supplier", "event_date", "delay_days"]},
    "inventory_shortage": {"critical": ["item", "quantity_required", "location"], "important": ["event_date"]},
    "supplier_issue": {"critical": ["supplier", "item"], "important": ["quantity_required", "event_date"]},
    "customer_commitment": {"critical": ["customer", "item", "quantity_required"], "important": ["event_date", "location"]},
    "approval_request": {"critical": ["item"], "important": ["cost_mentioned", "decision_deadline"]},
    "quality_issue": {"critical": ["item", "location"], "important": ["quantity_required", "supplier"]},
    "other": {"critical": [], "important": ["item", "location"]},
}

HUMAN_NAMES = {
    "item": "which item/SKU is affected",
    "quantity_required": "how many units are required",
    "location": "which store or location is affected",
    "supplier": "which supplier is involved",
    "event_date": "when the stock is required (event date)",
    "delay_days": "how long the delay is",
    "customer": "which customer the commitment is for",
    "cost_mentioned": "the cost involved",
    "decision_deadline": "the decision deadline",
}


def for_category(category: str) -> tuple[list[str], list[str]]:
    r = REQUIRED.get(category, REQUIRED["other"])
    return r["critical"], r["important"]
