"""Small circuit breaker: CLOSED -> OPEN after N consecutive failures -> HALF_OPEN after cooldown.

Per provider, in-memory per process. If the client runs several copies, shared state (e.g. Redis)
is a later step, see the private plan.
"""

import time
from collections.abc import Callable


class CircuitBreaker:
    def __init__(
        self,
        failure_threshold: int = 3,
        cooldown_s: float = 30.0,
        clock: Callable[[], float] = time.monotonic,
    ):
        self.failure_threshold = failure_threshold
        self.cooldown_s = cooldown_s
        self._clock = clock
        self._failures = 0
        self._opened_at: float | None = None
        self._probe_in_flight = False

    @property
    def state(self) -> str:
        if self._opened_at is None:
            return "CLOSED"
        if self._clock() - self._opened_at >= self.cooldown_s:
            return "HALF_OPEN"
        return "OPEN"

    def allow(self) -> bool:
        state = self.state
        if state == "CLOSED":
            return True
        if state == "HALF_OPEN" and not self._probe_in_flight:
            self._probe_in_flight = True  # let exactly one probe through
            return True
        return False

    def record_success(self) -> None:
        self._failures = 0
        self._opened_at = None
        self._probe_in_flight = False

    def record_failure(self) -> None:
        self._probe_in_flight = False
        self._failures += 1
        if self._opened_at is not None or self._failures >= self.failure_threshold:
            self._opened_at = self._clock()  # (re)open

    def record_neutral(self) -> None:
        """Outcome that says nothing about provider health (e.g. our own rate limit)."""
        self._probe_in_flight = False
