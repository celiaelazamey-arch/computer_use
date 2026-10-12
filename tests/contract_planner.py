"""The behavioural contract every local planner provider must satisfy.

Subclassing :class:`~cela_sh.planner.Planner` only proves a provider has the
right method name. This suite proves it behaves safely: it validates input,
never writes to memory during planning, enforces ``dry_run=True`` and
``execution_enabled=False``, applies the deterministic risk/shell policy
(including uniform ``sudo`` blocking), and raises :class:`PlannerValidationError`
on malformed model output instead of returning a fallback plan.
"""

from typing import Any
from unittest import mock

from cela_sh.memory import MemoryStore, MockMemoryStore
from cela_sh.planner import (
    Plan,
    PlanStatus,
    Planner,
    PlannerValidationError,
    RiskLevel,
    satisfies_planner_contract,
)


class PlannerContract:
    """Guarantees that are identical for every local planner provider in the repository."""

    def make_planner(
        self,
        *,
        memory_store: MemoryStore | None = None,
        raw_response: str | None = None,
    ) -> Any:
        """Return an offline-configured planner instance. Called once per test."""
        raise NotImplementedError("a contract test must provide make_planner()")

    def test_satisfies_the_planner_contract(self):
        planner = self.make_planner()
        self.assertTrue(
            satisfies_planner_contract(planner),
            "a planner provider must expose generate_plan",
        )
        self.assertIsInstance(planner, Planner)

    def test_rejects_blank_or_oversized_goal_and_context(self):
        planner = self.make_planner()
        for goal in ("", "   ", "x" * 2_001):
            with self.subTest(goal_len=len(goal)), self.assertRaises(PlannerValidationError):
                planner.generate_plan(goal)
        with self.assertRaises(PlannerValidationError):
            planner.generate_plan("Read the page", screen_context="x" * 8_001)

    def test_returns_dry_run_plan_with_execution_disabled(self):
        planner = self.make_planner()
        plan = planner.generate_plan("Read a public article about local-first software")

        self.assertIsInstance(plan, Plan)
        self.assertTrue(plan.dry_run)
        self.assertFalse(plan.execution_enabled)
        self.assertEqual(plan.status, PlanStatus.READY)
        self.assertFalse(plan.requires_confirmation)

    def test_sensitive_goal_requires_confirmation(self):
        planner = self.make_planner()
        plan = planner.generate_plan("Delete the selected file")

        self.assertEqual(plan.status, PlanStatus.REQUIRES_CONFIRMATION)
        self.assertTrue(plan.requires_confirmation)
        self.assertTrue(plan.dry_run)
        self.assertFalse(plan.execution_enabled)

    def test_sudo_and_shell_commands_are_blocked(self):
        planner = self.make_planner()
        plan = planner.generate_plan("run sudo apt update")

        self.assertEqual(plan.status, PlanStatus.BLOCKED)
        self.assertTrue(plan.requires_confirmation)
        self.assertIn(RiskLevel.BLOCKED, [step.risk for step in plan.steps])
        self.assertTrue(plan.dry_run)
        self.assertFalse(plan.execution_enabled)

    def test_rejects_invalid_model_output_instead_of_returning_fallback(self):
        planner = self.make_planner(raw_response='{"analysis": "bad", "steps": []}')
        with self.assertRaises(PlannerValidationError):
            planner.generate_plan("Search for an article")

    def test_memory_requires_session_id_and_never_writes_during_planning(self):
        store = MockMemoryStore()
        store.add_memory("session-a", "Prior local planning note")
        planner = self.make_planner(memory_store=store)

        for bad_session in (None, "", "   "):
            with self.subTest(session_id=bad_session), self.assertRaises(PlannerValidationError):
                planner.generate_plan("Continue planning", session_id=bad_session)

        with mock.patch.object(store, "add_memory", wraps=store.add_memory) as add_memory:
            plan = planner.generate_plan("Prior local planning", session_id="session-a")

        add_memory.assert_not_called()
        self.assertTrue(plan.dry_run)
        self.assertFalse(plan.execution_enabled)
