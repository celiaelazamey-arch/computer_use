import io
import json
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest.mock import Mock, patch

from cela_sh.cli import main
from cela_sh.planner.client import (
    PlannerConnectionError,
    PlannerError,
    PlannerMemoryError,
    PlannerResponseError,
    PlannerValidationError,
)
from cela_sh.planner.models import Action, Plan, PlanStatus, PlannedStep, RiskLevel
from cela_sh.settings import ConfigurationError, Settings


def ready_plan() -> Plan:
    return Plan(
        analysis="Search for a relevant article.",
        steps=[
            PlannedStep(
                action=Action.SEARCH_WEB,
                target="search engine",
                value="agent architecture",
                reason="Find a relevant article",
                risk=RiskLevel.LOW,
                requires_confirmation=False,
                policy_reasons=[],
            )
        ],
        status=PlanStatus.READY,
        requires_confirmation=False,
    )


class CliContractTests(unittest.TestCase):
    def test_invokes_planner_and_writes_only_structured_json_to_stdout(self):
        plan = ready_plan()
        planner = Mock()
        planner.generate_plan.return_value = plan
        stdout = io.StringIO()
        stderr = io.StringIO()
        settings = Settings()

        with patch("cela_sh.cli.Settings.from_env", return_value=settings), patch(
            "cela_sh.cli.create_planner", return_value=planner
        ) as factory:
            with redirect_stdout(stdout), redirect_stderr(stderr):
                exit_code = main(
                    ["--goal", "Find an article", "--context", "Home page", "--model", "qwen", "--session-id", "run-a"]
                )

        self.assertEqual(exit_code, 0)
        factory.assert_called_once_with(settings, model="qwen")
        planner.generate_plan.assert_called_once_with("Find an article", "Home page", session_id="run-a")
        output = json.loads(stdout.getvalue())
        self.assertEqual(output, json.loads(plan.model_dump_json(indent=2)))
        self.assertTrue(output["dry_run"])
        self.assertFalse(output["execution_enabled"])
        self.assertEqual(stderr.getvalue(), "")

    def test_defaults_model_and_context(self):
        planner = Mock()
        planner.generate_plan.return_value = ready_plan()
        stdout = io.StringIO()
        settings = Settings()

        with patch("cela_sh.cli.Settings.from_env", return_value=settings), patch(
            "cela_sh.cli.create_planner", return_value=planner
        ) as factory:
            with redirect_stdout(stdout):
                exit_code = main(["--goal", "Find an article"])

        self.assertEqual(exit_code, 0)
        factory.assert_called_once_with(settings, model=None)
        planner.generate_plan.assert_called_once_with("Find an article", "", session_id=None)

    def test_usage_errors_are_json_and_exit_two(self):
        stderr = io.StringIO()
        with redirect_stderr(stderr):
            with self.assertRaises(SystemExit) as raised:
                main([])

        self.assertEqual(raised.exception.code, 2)
        error = json.loads(stderr.getvalue())["error"]
        self.assertEqual(error["code"], "cli_usage_error")
        self.assertIn("usage", error)

    def test_blank_goal_is_rejected_before_planner_invocation(self):
        stderr = io.StringIO()
        with patch("cela_sh.cli.create_planner") as factory:
            with redirect_stderr(stderr):
                with self.assertRaises(SystemExit) as raised:
                    main(["--goal", "   "])

        self.assertEqual(raised.exception.code, 2)
        self.assertEqual(json.loads(stderr.getvalue())["error"]["code"], "cli_usage_error")
        factory.assert_not_called()

    def test_invalid_runtime_configuration_is_json_error(self):
        stderr = io.StringIO()
        with patch("cela_sh.cli.Settings.from_env", side_effect=ConfigurationError("bad settings")), patch(
            "cela_sh.cli.create_planner"
        ) as factory:
            with redirect_stderr(stderr):
                exit_code = main(["--goal", "Find an article"])

        self.assertEqual(exit_code, 2)
        self.assertEqual(json.loads(stderr.getvalue())["error"]["code"], "configuration_error")
        factory.assert_not_called()

    def test_planner_errors_have_stable_json_and_exit_codes(self):
        cases = (
            (PlannerConnectionError("offline"), 3, "planner_connection_error"),
            (PlannerResponseError("bad response"), 4, "planner_response_error"),
            (PlannerValidationError("invalid plan"), 5, "planner_validation_error"),
            (PlannerMemoryError("memory unavailable"), 6, "planner_memory_error"),
            (PlannerError("internal planner failure"), 1, "planner_error"),
        )
        for exception, expected_exit, expected_code in cases:
            with self.subTest(expected_code=expected_code):
                planner = Mock()
                planner.generate_plan.side_effect = exception
                stdout = io.StringIO()
                stderr = io.StringIO()
                settings = Settings()
                with patch("cela_sh.cli.Settings.from_env", return_value=settings), patch(
                    "cela_sh.cli.create_planner", return_value=planner
                ):
                    with redirect_stdout(stdout), redirect_stderr(stderr):
                        exit_code = main(["--goal", "Find an article"])

                self.assertEqual(exit_code, expected_exit)
                self.assertEqual(stdout.getvalue(), "")
                self.assertEqual(json.loads(stderr.getvalue())["error"]["code"], expected_code)

    def test_keyboard_interrupt_returns_130_and_json_error(self):
        planner = Mock()
        planner.generate_plan.side_effect = KeyboardInterrupt
        stderr = io.StringIO()
        with patch("cela_sh.cli.Settings.from_env", return_value=Settings()), patch(
            "cela_sh.cli.create_planner", return_value=planner
        ):
            with redirect_stderr(stderr):
                exit_code = main(["--goal", "Find an article"])

        self.assertEqual(exit_code, 130)
        self.assertEqual(json.loads(stderr.getvalue())["error"]["code"], "interrupted")


if __name__ == "__main__":
    unittest.main()
