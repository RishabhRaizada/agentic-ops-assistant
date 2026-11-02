"""Single source of time. Supports a demo offset so SLA escalation can be shown on camera
("advance clock +3h") without waiting. Every SLA/escalation decision reads time from here."""

import threading
from datetime import UTC, datetime, timedelta

from app.config import get_settings

_lock = threading.Lock()
_offset = timedelta(0)


def set_offset(seconds: float) -> None:
    global _offset
    with _lock:
        _offset = timedelta(seconds=seconds)


def offset_seconds() -> float:
    return _offset.total_seconds()


def now() -> datetime:
    """Current time in UTC, shifted by the demo offset."""
    return datetime.now(UTC) + _offset


def local_now() -> datetime:
    return now().astimezone(get_settings().tz)


def to_local(dt: datetime) -> datetime:
    return dt.astimezone(get_settings().tz)
