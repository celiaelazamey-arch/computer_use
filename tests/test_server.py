"""Offline HTTP contract tests, using real vec0 and deterministic embeddings."""

import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import Mock, patch

from fastapi.testclient import TestClient

from cela_sh import OllamaPlanner, Settings, SqliteVecMemoryStore
from cela_sh.memory import MemoryStoreError
from cela_sh.planner.client import (
    PlannerConnectionError, PlannerError, PlannerMemoryError, PlannerResponseError, PlannerValidationError,
)
from cela_sh.server import create_app

from fakes import DeterministicEmbedding


RAW_PLAN = {
    "analysis": "Read the public page.",
    "steps": [{"action": "OBSERVE", "target": "page", "value": "", "reason": "Read it"}],
}


class ServerTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path = str(Path(directory.name) / "api.db")
        self.embedding = DeterministicEmbedding()
        self.store = SqliteVecMemoryStore(self.path, self.embedding)
        self.planner = OllamaPlanner(memory_store=self.store)
        self.addCleanup(self.planner._session.close)
        self.settings = Settings(memory_backend="sqlite-vec", sqlite_vec_path=self.path)
        self.app = create_app(self.settings, self.planner)
        self.client = self.enterContext(TestClient(self.app))
        response = Mock(status_code=200)
        response.raise_for_status.return_value = None
        response.json.return_value = {"response": json.dumps(RAW_PLAN)}
        self.post = self.enterContext(patch.object(self.planner._session, "post", return_value=response))

    def test_health_is_liveness_without_ollama_or_implicit_writes(self):
        response = self.client.get("/health")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {
            "status": "ok", "memory_backend": "sqlite-vec", "dry_run": True, "execution_enabled": False,
        })
        self.post.assert_not_called()
        self.assertEqual(self.embedding.inputs, [])
        with closing(sqlite3.connect(self.path)) as conn:
            self.assertEqual(conn.execute("SELECT count(*) FROM memories").fetchone()[0], 0)

    def test_explicit_memory_write_and_scoped_native_search(self):
        payload = {"session_id": "session-a", "content": "ذاكرة عربية Ollama", "metadata": {"source": "test"}}
        response = self.client.post("/memory", json=payload)
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.json(), {"stored": True})
        self.client.post("/memory", json={"session_id": "session-b", "content": "Ollama private notes"})

        response = self.client.post("/memory/search", json={"session_id": "session-a", "query": "Ollama", "limit": 2})
        self.assertEqual(response.status_code, 200)
        result = response.json()[0]
        self.assertEqual(set(result), {"session_id", "content", "metadata", "score"})
        self.assertEqual(result["session_id"], "session-a")
        self.assertEqual(result["content"], payload["content"])
        self.assertEqual(result["metadata"], payload["metadata"])
        self.assertEqual(len(response.json()), 1)
        self.post.assert_not_called()

    def test_plan_uses_memory_but_never_stores_a_plan(self):
        self.store.add_memory("session-a", "Ollama planning history")
        self.store.add_memory("session-b", "other session private history")
        response = self.client.post("/plan", json={"goal": "Read a public page", "session_id": "session-a"})
        self.assertEqual(response.status_code, 200)
        plan = response.json()
        self.assertTrue(plan["dry_run"])
        self.assertFalse(plan["execution_enabled"])
        self.assertEqual(plan["status"], "ready")
        prompt = self.post.call_args.kwargs["json"]["prompt"]
        self.assertIn("Ollama planning history", prompt)
        self.assertNotIn("other session private history", prompt)
        self.assertFalse(self.post.call_args.kwargs["allow_redirects"])
        with closing(sqlite3.connect(self.path)) as conn:
            self.assertEqual(conn.execute("SELECT count(*) FROM memories").fetchone()[0], 2)

    def test_policy_still_requires_confirmation_for_sensitive_goals(self):
        response = self.client.post("/plan", json={"goal": "Delete the document", "session_id": "session-a"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "requires_confirmation")
        self.assertTrue(response.json()["requires_confirmation"])
        self.assertFalse(response.json()["execution_enabled"])

    def test_memory_enabled_plan_requires_a_session(self):
        response = self.client.post("/plan", json={"goal": "Read a public page"})
        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()["error"]["code"], "planner_validation_error")
        self.post.assert_not_called()

    def test_invalid_requests_stop_before_embeddings_or_ollama(self):
        requests = (
            ("/plan", {"goal": " "}),
            ("/plan", {"goal": "x" * 2001}),
            ("/plan", {"goal": "Read", "session_id": " "}),
            ("/plan", {"goal": "Read", "screen_context": "x" * 8001}),
            ("/plan", {"goal": "Read", "execution_enabled": True}),
            ("/plan", {"goal": "Read", "dry_run": False}),
            ("/memory", {"session_id": "s", "content": " "}),
            ("/memory", {"session_id": "s", "content": "x" * 8001}),
            ("/memory", {"session_id": " ", "content": "usable"}),
            ("/memory", {"session_id": "s", "content": "usable", "metadata": []}),
            ("/memory/search", {"session_id": "s", "query": " "}),
            ("/memory/search", {"session_id": "s", "query": "usable", "limit": True}),
            ("/memory/search", {"session_id": "s", "query": "usable", "limit": 0}),
            ("/memory/search", {"session_id": "s", "query": "usable", "limit": 101}),
        )
        for path, payload in requests:
            with self.subTest(path=path, fields=list(payload)):
                self.assertEqual(self.client.post(path, json=payload).status_code, 422)
        self.assertEqual(self.embedding.inputs, [])
        self.post.assert_not_called()
        self.assertEqual(self.client.post("/execute", json={}).status_code, 404)

    def test_planner_errors_have_explicit_http_status_and_no_substitute_plan(self):
        errors = (
            (PlannerConnectionError("offline"), 503, "planner_connection_error"),
            (PlannerResponseError("bad JSON"), 502, "planner_response_error"),
            (PlannerValidationError("bad schema"), 422, "planner_validation_error"),
            (PlannerMemoryError("lookup failed"), 503, "planner_memory_error"),
            (PlannerError("failed"), 500, "planner_error"),
        )
        for error, status, code in errors:
            with self.subTest(code=code), patch.object(self.planner, "generate_plan", side_effect=error):
                response = self.client.post("/plan", json={"goal": "Read", "session_id": "session-a"})
                self.assertEqual(response.status_code, status)
                self.assertEqual(response.json(), {"error": {"code": code, "message": str(error)}})
                self.assertNotIn("steps", response.json())

    def test_memory_lookup_failure_prevents_the_model_call(self):
        with patch.object(self.store, "search_memory", side_effect=MemoryStoreError("unavailable")):
            response = self.client.post("/plan", json={"goal": "Read", "session_id": "session-a"})
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()["error"]["code"], "planner_memory_error")
        self.post.assert_not_called()

    def test_memory_errors_are_not_silent_empty_results(self):
        for path, method, payload in (
            ("/memory", "add_memory", {"session_id": "s", "content": "usable"}),
            ("/memory/search", "search_memory", {"session_id": "s", "query": "usable"}),
        ):
            with self.subTest(path=path), patch.object(self.store, method, side_effect=MemoryStoreError("unavailable")):
                response = self.client.post(path, json=payload)
                self.assertEqual(response.status_code, 503)
                self.assertEqual(response.json()["error"]["code"], "memory_store_error")
        with patch.object(self.store, "add_memory", return_value=False):
            self.assertEqual(self.client.post("/memory", json={"session_id": "s", "content": "usable"}).status_code, 503)

    def test_store_validation_errors_are_422_not_500(self):
        with patch.object(self.store, "add_memory", side_effect=ValueError("bad metadata")):
            response = self.client.post("/memory", json={"session_id": "s", "content": "usable"})
        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()["error"]["code"], "memory_validation_error")

    def test_disabled_memory_returns_conflict_but_plan_still_works(self):
        planner = OllamaPlanner()
        self.addCleanup(planner._session.close)
        with TestClient(create_app(Settings(), planner)) as client, patch.object(planner._session, "post", self.post):
            for path, payload in (
                ("/memory", {"session_id": "s", "content": "usable"}),
                ("/memory/search", {"session_id": "s", "query": "usable"}),
            ):
                response = client.post(path, json=payload)
                self.assertEqual(response.status_code, 409)
                self.assertEqual(response.json()["error"]["code"], "memory_disabled")
            self.assertEqual(client.post("/plan", json={"goal": "Read"}).status_code, 200)

    def test_creating_the_app_defers_composition_until_startup(self):
        with patch("cela_sh.server.create_planner", return_value=self.planner) as factory:
            application = create_app(self.settings)
            factory.assert_not_called()
            with TestClient(application) as client:
                self.assertEqual(client.get("/health").status_code, 200)
                factory.assert_called_once_with(self.settings)
        self.post.assert_not_called()

    def test_startup_initializes_sqlite_without_contacting_ollama(self):
        with patch("requests.Session.post") as network:
            with TestClient(create_app(self.settings)) as client:
                self.assertEqual(client.get("/health").status_code, 200)
            network.assert_not_called()

    def test_unusable_storage_fails_startup_instead_of_claiming_healthy(self):
        settings = Settings(memory_backend="sqlite-vec", sqlite_vec_path=str(Path(self.path).parent / "missing" / "db"))
        with self.assertRaises(MemoryStoreError):
            with TestClient(create_app(settings)):
                self.fail("unusable memory must prevent startup")


if __name__ == "__main__":
    unittest.main()
