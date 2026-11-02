"""Minimal circuit breaker: after N consecutive failures the dependency is skipped for a cool-down,
so a dead inventory system costs one fast failure instead of a timeout per request."""

import threading
import time


class CircuitOpen(Exception):
    pass


class Breaker:
    def __init__(self, name: str, threshold: int = 3, cooldown_s: float = 30.0):
        self.name = name
        self.threshold = threshold
        self.cooldown_s = cooldown_s
        self.failures = 0
        self.opened_at: float | None = None
        self._lock = threading.Lock()

    @property
    def state(self) -> str:
        if self.opened_at is None:
            return "closed"
        if time.monotonic() - self.opened_at >= self.cooldown_s:
            return "half_open"
        return "open"

    def before(self) -> None:
        if self.state == "open":
            raise CircuitOpen(f"{self.name} circuit open (cooling down after repeated failures)")

    def success(self) -> None:
        with self._lock:
            self.failures = 0
            self.opened_at = None

    def failure(self) -> None:
        with self._lock:
            self.failures += 1
            if self.failures >= self.threshold:
                self.opened_at = time.monotonic()

    def reset(self) -> None:
        self.success()


BREAKERS = {
    "inventory": Breaker("inventory"),
    "mail": Breaker("mail", threshold=5, cooldown_s=20),
    "llm": Breaker("llm", threshold=4, cooldown_s=20),
}
