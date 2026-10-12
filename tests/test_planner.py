import json
import unittest
from unittest.mock import Mock, patch

import requests
from pydantic import ValidationError

from cela_sh.planner.client import (
    OllamaPlanner,
    PlannerConnectionError,
    PlannerMemoryError,
    PlannerResponseError,
    PlannerValidationError,
)
from cela_sh.memory import MemoryStore, MemoryStoreError, MockMemoryStore
from cela_sh.planner.models import PlanStatus, RawPlan


def sample_raw_plan(action="SEARCH_WEB", target="", value="agent architecture", reason="Find a relevant article"):
    return {
        "analysis": "Search for a relevant article.",
        "steps": [{"action": action, "target": target, "value": value, "reason": reason}],
    }


class PlannerTests(unittest.TestCase):
    def test_abstract_planner_contract_rejects_incomplete_providers(self):
        from cela_sh.planner import Planner

        class IncompletePlanner(Planner):
            pass

        with self.assertRaises(TypeError):
            IncompletePlanner()

    def test_ollama_endpoint_must_remain_on_loopback_http(self):
        for base_url in ("http://192.168.1.8:11434", "https://127.0.0.1:11434", "http://localhost:11434/path"):
            with self.subTest(base_url=base_url), self.assertRaises(ValueError):
                OllamaPlanner(base_url=base_url)

    def test_strict_schema_rejects_unknown_actions_and_extra_fields(self):
        invalid_action = sample_raw_plan(action="RUN_COMMAND")
        with self.assertRaises(ValidationError):
            RawPlan.model_validate_json(json.dumps(invalid_action))

        with self.assertRaises(ValidationError):
            RawPlan.model_validate_json(json.dumps(sample_raw_plan() | {"execution_enabled": True}))

    def test_stop_must_be_last(self):
        raw = {
            "analysis": "Invalid order.",
            "steps": [
                {"action": "STOP", "target": "", "value": "", "reason": "Stop."},
                {"action": "OBSERVE", "target": "page", "value": "", "reason": "Observe."},
            ],
        }
        with self.assertRaises(ValidationError):
            RawPlan.model_validate_json(json.dumps(raw))

    def test_sensitive_goal_requires_confirmation(self):
        response = Mock()
        response.status_code = 200
        response.json.return_value = {"response": json.dumps(sample_raw_plan())}
        planner = OllamaPlanner()
        with patch.object(planner._session, "post", return_value=response) as post:
            plan = planner.generate_plan("Delete the selected file", "A file manager is open")

        self.assertEqual(plan.status, PlanStatus.REQUIRES_CONFIRMATION)
        self.assertTrue(plan.requires_confirmation)
        self.assertTrue(plan.steps[0].requires_confirmation)
        self.assertTrue(plan.dry_run)
        self.assertFalse(plan.execution_enabled)
        self.assertEqual(post.call_args.kwargs["timeout"], (3.05, 60.0))
        self.assertFalse(post.call_args.kwargs["allow_redirects"])
        self.assertFalse(planner._session.trust_env)

    def test_search_query_is_normalized_into_value(self):
        response = Mock()
        response.status_code = 200
        response.json.return_value = {
            "response": json.dumps(sample_raw_plan(target="مقال عربي عن الخصوصية", value=""))
        }
        planner = OllamaPlanner()
        with patch.object(planner._session, "post", return_value=response):
            plan = planner.generate_plan("Find an article about privacy")

        self.assertEqual(plan.steps[0].target, "browser search field")
        self.assertEqual(plan.steps[0].value, "مقال عربي عن الخصوصية")

    def test_shell_target_is_blocked_by_independent_policy(self):
        response = Mock()
        response.status_code = 200
        response.json.return_value = {
            "response": json.dumps(
                sample_raw_plan(action="OPEN_APP", target="PowerShell", value="", reason="Run a command")
            )
        }
        planner = OllamaPlanner()
        with patch.object(planner._session, "post", return_value=response):
            plan = planner.generate_plan("Open the requested app")

        self.assertEqual(plan.status, PlanStatus.BLOCKED)
        self.assertTrue(plan.steps[0].requires_confirmation)

    def test_timeout_is_raised_not_returned_as_a_plan(self):
        planner = OllamaPlanner()
        with patch.object(planner._session, "post", side_effect=requests.Timeout):
            with self.assertRaises(PlannerConnectionError):
                planner.generate_plan("Search for an article")

    def test_redirect_response_is_rejected(self):
        response = Mock()
        response.status_code = 302
        planner = OllamaPlanner()
        with patch.object(planner._session, "post", return_value=response):
            with self.assertRaises(PlannerResponseError):
                planner.generate_plan("Search for an article")

    def test_missing_model_error_includes_pull_command(self):
        response = Mock()
        response.status_code = 404
        planner = OllamaPlanner(model="qwen2.5:1.5b")
        with patch.object(planner._session, "post", return_value=response):
            with self.assertRaisesRegex(PlannerResponseError, "ollama pull qwen2.5:1.5b"):
                planner.generate_plan("Search for an article")

    def test_invalid_model_output_is_raised_not_returned_as_a_plan(self):
        response = Mock()
        response.status_code = 200
        response.json.return_value = {"response": '{"analysis":"bad","steps":[]}'}
        planner = OllamaPlanner()
        with patch.object(planner._session, "post", return_value=response):
            with self.assertRaises(PlannerValidationError):
                planner.generate_plan("Search for an article")

    def test_injected_memory_is_scoped_and_passed_as_untrusted_context(self):
        store = MockMemoryStore()
        store.add_memory("session-a", "Agent planning used local Ollama last time")
        store.add_memory("session-b", "Agent planning used a private cloud key")
        response = Mock()
        response.status_code = 200
        response.json.return_value = {"response": json.dumps(sample_raw_plan())}
        planner = OllamaPlanner(memory_store=store, memory_limit=2)

        with patch.object(planner._session, "post", return_value=response) as post:
            planner.generate_plan("agent planning", session_id="session-a")

        prompt = post.call_args.kwargs["json"]["prompt"]
        prompt_data = json.loads(prompt.split("\n", 1)[1])
        self.assertEqual(
            prompt_data["relevant_memories"],
            [{"content": "Agent planning used local Ollama last time"}],
        )
        self.assertNotIn("session-a", prompt)
        self.assertEqual(len(store._storage), 2)

    def test_memory_requires_session_scope_and_fails_closed(self):
        store = MockMemoryStore()
        planner = OllamaPlanner(memory_store=store)
        with patch.object(planner._session, "post") as post:
            with self.assertRaises(PlannerValidationError):
                planner.generate_plan("agent planning")
        post.assert_not_called()

        class UnavailableStore(MemoryStore):
            def add_memory(self, session_id, content, metadata=None):
                return False

            def search_memory(self, query, session_id, limit=3):
                raise MemoryStoreError("offline")

        planner = OllamaPlanner(memory_store=UnavailableStore())
        with patch.object(planner._session, "post") as post:
            with self.assertRaises(PlannerMemoryError):
                planner.generate_plan("agent planning", session_id="session-a")
        post.assert_not_called()


if __name__ == "__main__":
    unittest.main()
