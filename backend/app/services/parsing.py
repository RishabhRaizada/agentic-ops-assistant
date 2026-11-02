"""Deterministic normalisation of values the LLM extracts as text. The model reads; code computes.
Quantities, money and dates are parsed here so arithmetic never depends on the model."""

import re
from datetime import datetime, timedelta

import dateparser

from app.config import get_settings

_WORD_NUMS = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9,
    "ten": 10, "eleven": 11, "twelve": 12, "fifteen": 15, "twenty": 20, "thirty": 30, "fifty": 50, "hundred": 100,
}


def normalize_ws(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip().lower()


def parse_int(text: str | None) -> int | None:
    if text is None:
        return None
    if isinstance(text, int | float):
        return int(text)
    t = text.replace(",", "")
    m = re.search(r"-?\d+(\.\d+)?", t)
    if m:
        return int(float(m.group(0)))
    for w, n in _WORD_NUMS.items():
        if re.search(rf"\b{w}\b", t.lower()):
            return n
    return None


def parse_money_inr(text: str | None) -> float | None:
    """Handles ₹, Rs, INR, lakh/lac, crore, k."""
    if text is None:
        return None
    if isinstance(text, int | float):
        return float(text)
    t = text.lower().replace(",", "")
    m = re.search(r"(\d+(\.\d+)?)\s*(lakh|lac|l\b|crore|cr\b|k\b|thousand)?", t)
    if not m:
        return None
    val = float(m.group(1))
    unit = m.group(3) or ""
    if unit in ("lakh", "lac", "l"):
        val *= 100_000
    elif unit in ("crore", "cr"):
        val *= 10_000_000
    elif unit in ("k", "thousand"):
        val *= 1_000
    return val


def parse_when(text: str | None, base: datetime) -> datetime | None:
    """Resolve phrases like '4:00 PM today', 'next week', 'in 10 days', '12 Nov' relative to when the
    email was received, in the business timezone. Returns an aware datetime or None."""
    if not text:
        return None
    tz = get_settings().tz
    local_base = base.astimezone(tz)
    t = text.strip().lower()
    t = re.sub(r"^(by|before|on|until|till|no later than)\s+", "", t)
    if "next week" in t:
        days_to_monday = (7 - local_base.weekday()) % 7 or 7
        return (local_base + timedelta(days=days_to_monday)).replace(hour=10, minute=0, second=0, microsecond=0)
    if t in ("eod", "end of day", "today eod", "eod today"):
        return local_base.replace(hour=18, minute=0, second=0, microsecond=0)
    if t in ("tomorrow", "tomorrow eod"):
        return (local_base + timedelta(days=1)).replace(hour=18, minute=0, second=0, microsecond=0)
    m = re.search(r"in (\d+) (day|days|week|weeks)", t)
    if m:
        n = int(m.group(1)) * (7 if "week" in m.group(2) else 1)
        return (local_base + timedelta(days=n)).replace(hour=10, minute=0, second=0, microsecond=0)
    parsed = dateparser.parse(
        t,
        settings={
            "RELATIVE_BASE": local_base.replace(tzinfo=None),
            "PREFER_DATES_FROM": "future",
            "TIMEZONE": get_settings().app_timezone,
            "RETURN_AS_TIMEZONE_AWARE": True,
        },
    )
    if parsed is None:
        return None
    return parsed.astimezone(tz)


def is_substring(quote: str | None, source: str) -> bool:
    if not quote:
        return False
    q = normalize_ws(quote.replace("^", " ")).strip(" .,'\"")
    return len(q) >= 2 and q in normalize_ws(source)
