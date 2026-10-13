"""Offline unit tests for the fault-isolation primitives (breaker + health)."""

import threading
import unittest

from cela_sh.resilience import CircuitBreaker, DependencyHealth


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class CircuitBreakerTests(unittest.TestCase):
    def test_constructor_rejects_invalid_parameters(self):
        for failure_threshold, cooldown in (
            (0, 1.0),
            (True, 1.0),
            (-1, 1.0),
            (3, 0.0),
            (3, float("nan")),
            (3, float("inf")),
            (3, -2),
        ):
            with self.subTest(failure_threshold=failure_threshold, cooldown=cooldown):
                with self.assertRaises(ValueError):
                    CircuitBreaker("x", failure_threshold, cooldown)
        with self.assertRaises(ValueError):
            CircuitBreaker("  ")

    def test_closed_allows_until_the_failure_threshold(self):
        clock = FakeClock()
        breaker = CircuitBreaker("planner", failure_threshold=3, cooldown_seconds=10.0, clock=clock)
        self.assertEqual(breaker.state, "closed")
        self.assertTrue(breaker.allow())
        breaker.record_failure()
        breaker.record_failure()
        self.assertEqual(breaker.state, "closed")
        self.assertEqual(breaker.consecutive_failures, 2)
        self.assertTrue(breaker.allow())
        breaker.record_failure()
        self.assertEqual(breaker.state, "open")
        self.assertFalse(breaker.allow())

    def test_a_success_resets_the_consecutive_failure_count(self):
        breaker = CircuitBreaker("planner", failure_threshold=3, cooldown_seconds=10.0)
        breaker.record_failure()
        breaker.record_success()
        breaker.record_failure()
        breaker.record_failure()
        self.assertEqual(breaker.state, "closed", "isolated failures must not accumulate")
        self.assertEqual(breaker.consecutive_failures, 2)

    def test_open_refuses_until_the_cooldown_elapses(self):
        clock = FakeClock()
        breaker = CircuitBreaker("planner", failure_threshold=1, cooldown_seconds=10.0, clock=clock)
        breaker.record_failure()
        self.assertEqual(breaker.state, "open")
        clock.advance(9.9)
        self.assertFalse(breaker.allow())
        clock.advance(0.1)
        self.assertTrue(breaker.allow(), "after the cooldown one probe must be let through")
        self.assertEqual(breaker.state, "half_open")
        self.assertFalse(breaker.allow(), "concurrent callers still fail fast during the probe")

    def test_probe_success_closes_the_circuit(self):
        clock = FakeClock()
        breaker = CircuitBreaker("planner", failure_threshold=1, cooldown_seconds=10.0, clock=clock)
        breaker.record_failure()
        clock.advance(10.0)
        self.assertTrue(breaker.allow())
        breaker.record_success()
        self.assertEqual(breaker.state, "closed")
        self.assertEqual(breaker.consecutive_failures, 0)
        self.assertTrue(breaker.allow())

    def test_probe_failure_reopens_for_a_full_cooldown(self):
        clock = FakeClock()
        breaker = CircuitBreaker("planner", failure_threshold=1, cooldown_seconds=10.0, clock=clock)
        breaker.record_failure()  # open at t=0
        clock.advance(10.0)
        self.assertTrue(breaker.allow())  # probe at t=10
        breaker.record_failure()  # probe failed: open again at t=10
        self.assertEqual(breaker.state, "open")
        clock.advance(9.9)
        self.assertFalse(breaker.allow())
        clock.advance(0.1)
        self.assertTrue(breaker.allow(), "the second probe happens at t=20, not t=19.9")

    def test_reset_closes_and_forgets_failures(self):
        breaker = CircuitBreaker("planner", failure_threshold=2, cooldown_seconds=10.0)
        breaker.record_failure()
        breaker.record_failure()
        self.assertEqual(breaker.state, "open")
        breaker.reset()
        self.assertEqual(breaker.state, "closed")
        self.assertEqual(breaker.consecutive_failures, 0)

    def test_breaker_stays_consistent_under_contention(self):
        breaker = CircuitBreaker("planner", failure_threshold=3, cooldown_seconds=0.01)
        errors: list[BaseException] = []

        def hammer() -> None:
            try:
                for _ in range(300):
                    if breaker.allow():
                        if (breaker.consecutive_failures + 1) % 2:
                            breaker.record_failure()
                        else:
                            breaker.record_success()
            except BaseException as exc:  # pragma: no cover - failure report only
                errors.append(exc)

        threads = [threading.Thread(target=hammer) for _ in range(8)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(errors, [])
        self.assertIn(breaker.state, ("closed", "open", "half_open"))


class DependencyHealthTests(unittest.TestCase):
    def test_unknown_before_any_outcome(self):
        health = DependencyHealth()
        self.assertEqual(health.state("planner"), "unknown")
        self.assertEqual(health.consecutive_failures("planner"), 0)
        self.assertEqual(health.snapshot(), {})

    def test_failure_and_recovery_are_recorded(self):
        health = DependencyHealth()
        health.record_failure("memory")
        self.assertEqual(health.state("memory"), "unavailable")
        health.record_failure("memory")
        self.assertEqual(health.consecutive_failures("memory"), 2)
        health.record_success("memory")
        self.assertEqual(health.state("memory"), "healthy")
        self.assertEqual(health.consecutive_failures("memory"), 0)
        snapshot = health.snapshot()
        self.assertEqual(snapshot["memory"], {"state": "healthy", "consecutive_failures": 0})

    def test_dependencies_are_tracked_independently(self):
        health = DependencyHealth()
        health.record_success("planner")
        health.record_failure("memory")
        self.assertEqual(health.state("planner"), "healthy")
        self.assertEqual(health.state("memory"), "unavailable")
        health.record_success("memory")
        self.assertEqual(health.state("memory"), "healthy")
        self.assertEqual(health.state("planner"), "healthy")

    def test_snapshot_is_a_copy(self):
        health = DependencyHealth()
        health.record_failure("planner")
        snapshot = health.snapshot()
        snapshot["planner"]["state"] = "healthy"
        self.assertEqual(health.state("planner"), "unavailable")


if __name__ == "__main__":
    unittest.main()
