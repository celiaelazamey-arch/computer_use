import io
import json
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest.mock import Mock, patch

from cela_sh.planner.client import PlannerConnectionError
from cela_sh.planner.models import Action, Plan, PlanStatus, PlannedStep, RiskLevel
from cela_sh.settings import Settings
from integration_plan_probe import main


def sample_plan() -> Plan:
    return Plan(
        analysis="Find a brief introductory article.",
        steps=[
            PlannedStep(
                action=Action.SEARCH_WEB,
                target="search engine",
                value="local-first software",
                reason="Find a concise overview.",
                risk=RiskLevel.LOW,
                requires_confirmation=False,
                policy_reasons=[],
            )
        ],
        status=PlanStatus.READY,
        requires_confirmation=False,
    )


class PlanProbeTests(unittest.TestCase):
    def test_emits_real_planning_contract_as_json(self):
        settings = Settings(memory_backend="qdrant")
        planner = Mock()
        planner.generate_plan.return_value = sample_plan()
        stdout = io.StringIO()
        stderr = io.StringIO()

        with patch("integration_plan_probe.Settings.from_env", return_value=settings), patch(
            "integration_plan_probe.OllamaPlanner", return_value=planner
        ) as planner_type:
            with redirect_stdout(stdout), redirect_stderr(stderr):
                exit_code = main()

        self.assertEqual(exit_code, 0)
        planner_type.assert_called_once_with(
            model=settings.planner_model,
            timeout=(settings.connect_timeout, settings.read_timeout),
            base_url=settings.ollama_url,
        )
        planner.generate_plan.assert_called_once()
        result = json.loads(stdout.getvalue())
        self.assertTrue(result["dry_run"])
        self.assertFalse(result["execution_enabled"])
        self.assertIn("PASS", stderr.getvalue())

    def test_returns_failure_without_a_plan_on_service_error(self):
        planner = Mock()
        planner.generate_plan.side_effect = PlannerConnectionError("offline")
        stderr = io.StringIO()

        with patch("integration_plan_probe.Settings.from_env", return_value=Settings()), patch(
            "integration_plan_probe.OllamaPlanner", return_value=planner
        ):
            with redirect_stderr(stderr):
                exit_code = main()

        self.assertEqual(exit_code, 1)
        self.assertTrue(stderr.getvalue().startswith("FAIL"))
