"""Process-local fault isolation primitives.

Two small, thread-safe building blocks that keep one dead or slow optional
dependency from dragging the rest of the service down with it:

* :class:`CircuitBreaker` — fail fast after repeated consecutive failures
  instead of stacking timeouts on a dead dependency, then probe once the
  cooldown has elapsed so the dependency can recover without an operator.
* :class:`DependencyHealth` — a passive view of dependency state derived from
  *real* request outcomes. It never performs I/O, so it cannot claim a
  dependency is healthy without evidence, and it recovers automatically the
  moment the next real call succeeds.

Both are per-process state only. They are deliberately not shared across
replicas: with multiple API replicas each replica isolates faults for its own
requests, and the view reported by ``/readyz`` is always "this process's
evidence", never a fabricated global status.
"""

import math
import threading
import time
from typing import Any


class CircuitBreaker:
    """A thread-safe closed/open/half-open circuit breaker.

    ``closed``: traffic flows. Consecutive failures are counted; reaching
    ``failure_threshold`` opens the circuit.
    ``open``: :meth:`allow` refuses traffic (callers fail fast) until the
    cooldown has elapsed.
    ``half_open``: after the cooldown, exactly one probe request is let
    through. A successful probe closes the circuit; a failed probe re-opens
    it for another full cooldown.

    Success and failure must be recorded by the caller that actually talked
    to the dependency (see the planner and memory routes in ``server.py``).
    Input-validation problems are not dependency failures and must not be
    recorded.
    """

    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"

    def __init__(
        self,
        name: str,
        failure_threshold: int = 3,
        cooldown_seconds: float = 10.0,
        *,
        clock: Any = time.monotonic,
    ) -> None:
        if not isinstance(name, str) or not name.strip():
            raise ValueError("breaker name must not be blank")
        if (
            isinstance(failure_threshold, bool)
            or not isinstance(failure_threshold, int)
            or failure_threshold < 1
        ):
            raise ValueError("failure_threshold must be a positive integer")
        if (
            isinstance(cooldown_seconds, bool)
            or not isinstance(cooldown_seconds, (int, float))
            or not math.isfinite(cooldown_seconds)
            or cooldown_seconds <= 0
        ):
            raise ValueError("cooldown_seconds must be a positive finite number")

        self.name = name.strip()
        self.failure_threshold = failure_threshold
        self.cooldown_seconds = float(cooldown_seconds)
        self._clock = clock
        self._lock = threading.Lock()
        self._state = self.CLOSED
        self._consecutive_failures = 0
        self._opened_at: float | None = None

    @property
    def state(self) -> str:
        with self._lock:
            return self._state

    @property
    def consecutive_failures(self) -> int:
        with self._lock:
            return self._consecutive_failures

    def allow(self) -> bool:
        """Return True when a real request may proceed.

        While open past the cooldown, the first caller transitions the
        breaker to half-open and becomes the single probe; every concurrent
        caller still fails fast.
        """
        with self._lock:
            if self._state == self.CLOSED:
                return True
            if self._state == self.OPEN and self._opened_at is not None:
                if self._clock() - self._opened_at >= self.cooldown_seconds:
                    self._state = self.HALF_OPEN
                    return True
            return False

    def record_success(self) -> None:
        with self._lock:
            self._consecutive_failures = 0
            self._state = self.CLOSED

    def record_failure(self) -> None:
        with self._lock:
            if self._state == self.HALF_OPEN:
                # The probe failed: back to open for another full cooldown.
                self._state = self.OPEN
                self._opened_at = self._clock()
                return
            self._consecutive_failures += 1
            if self._state == self.CLOSED and self._consecutive_failures >= self.failure_threshold:
                self._state = self.OPEN
                self._opened_at = self._clock()

    def reset(self) -> None:
        """Close the circuit and forget the failure count (operator/test tool)."""
        with self._lock:
            self._state = self.CLOSED
            self._consecutive_failures = 0
            self._opened_at = None


class DependencyHealth:
    """Passive per-process dependency state from real request outcomes.

    ``state(name)`` reports:

    * ``unknown`` — no completed call to that dependency yet (an honest
      "no evidence", never an assumed healthy);
    * ``healthy`` — the most recent completed call succeeded;
    * ``unavailable`` — the most recent completed call failed (with the
      running consecutive-failure count).

    Recording happens in the request path of the caller that actually
    performed the I/O, so the view cannot claim availability that has not
    been measured, and recovery is automatic: the next successful call flips
    the state back to healthy.
    """

    UNKNOWN = "unknown"
    HEALTHY = "healthy"
    UNAVAILABLE = "unavailable"

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._entries: dict[str, dict[str, Any]] = {}

    def record_success(self, name: str) -> None:
        with self._lock:
            self._entries[name] = {"state": self.HEALTHY, "consecutive_failures": 0}

    def record_failure(self, name: str) -> None:
        with self._lock:
            entry = self._entries.get(name)
            if entry is None:
                entry = {"state": self.UNKNOWN, "consecutive_failures": 0}
                self._entries[name] = entry
            entry["state"] = self.UNAVAILABLE
            entry["consecutive_failures"] += 1

    def state(self, name: str) -> str:
        with self._lock:
            entry = self._entries.get(name)
            return self.UNKNOWN if entry is None else entry["state"]

    def consecutive_failures(self, name: str) -> int:
        with self._lock:
            entry = self._entries.get(name)
            return 0 if entry is None else entry["consecutive_failures"]

    def snapshot(self) -> dict[str, dict[str, Any]]:
        with self._lock:
            return {name: dict(entry) for name, entry in sorted(self._entries.items())}
