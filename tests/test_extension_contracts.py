"""Guards that keep extending the repository additive instead of breaking.

Two things are enforced here:

1. **The extension path works.** A brand-new memory backend is added the way a
   contributor would add one — a new class plus one ``register_memory_backend``
   call — and is then wired through settings, the factory, and the planner
   without editing any of them.
2. **No backend can skip the rules.** Every registered backend must be covered
   by the shared contract suite, and the safety invariants that define this
   system (dry-run only, loopback only, session-scoped memory, no automatic
   writes) are asserted independently of the model.
"""

import json
import sys
import unittest
from copy import deepcopy
from pathlib import Path
from typing import Any
from unittest import mock

import requests
from pydantic import ValidationError

# Keep the offline helpers importable under every runner: unittest discovery,
# pytest, `python -m unittest tests.test_extension_contracts`, and direct
# execution. Discovery already puts this directory on sys.path, so it is a no-op
# there.
sys.path.insert(0, str(Path(__file__).resolve().parent))

from contract_memory_store import MemoryStoreContract  # noqa: E402
from fakes import DeterministicEmbedding, FakeQdrantSession  # noqa: E402

from cela_sh import (  # noqa: E402
    Action,
    ConfigurationError,
    MemoryStore,
    MockMemoryStore,
    OllamaPlanner,
    Plan,
    PlanStatus,
    QdrantMemoryStore,
    RiskLevel,
    Settings,
    create_planner,
)
from cela_sh.memory import (  # noqa: E402
    build_memory_store,
    is_known_memory_backend,
    memory_backend_names,
    register_memory_backend,
    registered_memory_backends,
    unregister_memory_backend,
)
from cela_sh.planner.models import PlannerInput, PlannedStep, RawPlan, RawStep  # noqa: E402
from cela_sh.planner.policy import apply_policy  # noqa: E402

#: Backends covered by a MemoryStoreContract suite in this module. Registering a
#: backend without adding it here fails
#: ``test_every_registered_backend_is_covered_by_a_contract_suite``.
COVERED_BACKENDS = frozenset({"qdrant", "example", "sqlite-vec"})

OBSERVE_STEP = {
    "action": Action.OBSERVE,
    "target": "page",
    "value": "",
    "reason": "Read it",
    "risk": RiskLevel.LOW,
    "requires_confirmation": False,
    "policy_reasons": [],
}


def ready_plan_payload() -> dict[str, Any]:
    return {
        "analysis": "Read a public page.",
        "steps": [PlannedStep(**OBSERVE_STEP)],
        "status": PlanStatus.READY,
        "requires_confirmation": False,
        "dry_run": True,
        "execution_enabled": False,
    }


class ExampleMemoryStore(MemoryStore):
    """A new backend written the way a contributor would write one.

    Deliberately minimal: a per-session dict. It exists to prove that reaching
    the planner requires no change to ``factory.py``, ``settings.py``, or the CLI.
    """

    def __init__(self, namespace: str = "example") -> None:
        self.namespace = namespace
        self._sessions: dict[str, list[dict[str, Any]]] = {}
        self.added: list[tuple[str, str]] = []

    def add_memory(self, session_id: str, content: str, metadata: dict[str, Any] | None = None) -> bool:
        if not session_id.strip():
            raise ValueError("session_id must not be blank")
        if not content.strip() or len(content) > 8_000:
            raise ValueError("content must contain 1 to 8000 characters")
        if metadata is not None and not isinstance(metadata, dict):
            raise ValueError("metadata must be a dictionary")
        clean_metadata = deepcopy(metadata or {})
        try:
            json.dumps(clean_metadata, ensure_ascii=False, allow_nan=False)
        except (TypeError, ValueError) as exc:
            raise ValueError("metadata must contain JSON-compatible values") from exc

        self._sessions.setdefault(session_id.strip(), []).append(
            {"session_id": session_id.strip(), "content": content, "metadata": clean_metadata}
        )
        self.added.append((session_id.strip(), content))
        return True

    def search_memory(self, query: str, session_id: str, limit: int = 3) -> list[dict[str, Any]]:
        if not query.strip():
            raise ValueError("query must not be blank")
        if not session_id.strip():
            raise ValueError("session_id must not be blank")
        if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1:
            raise ValueError("limit must be a positive integer")

        tokens = set(query.casefold().split())
        matches = []
        for memory in self._sessions.get(session_id.strip(), []):
            overlap = tokens & set(memory["content"].casefold().split())
            if overlap:
                matches.append(
                    {**memory, "metadata": deepcopy(memory["metadata"]), "score": len(overlap) / len(tokens)}
                )
        return matches[:limit]


def _build_example_store(config: Settings, timeout: tuple[float, float]) -> ExampleMemoryStore:
    """A backend builder reads settings and returns a store. Nothing else."""
    return ExampleMemoryStore(namespace=config.qdrant_collection)


class MockMemoryStoreContractTests(MemoryStoreContract, unittest.TestCase):
    def make_store(self) -> Any:
        return MockMemoryStore()


class QdrantMemoryStoreContractTests(MemoryStoreContract, unittest.TestCase):
    """Runs the same contract against the real adapter over an offline double."""

    def make_store(self) -> Any:
        return QdrantMemoryStore(
            collection_name="contract_test",
            embedding_provider=DeterministicEmbedding(),
            session=FakeQdrantSession(),
        )


class ExampleMemoryStoreContractTests(MemoryStoreContract, unittest.TestCase):
    def make_store(self) -> Any:
        return ExampleMemoryStore()


class SqliteVecMemoryStoreContractTests(MemoryStoreContract, unittest.TestCase):
    """Runs the shared contract against the sqlite-vec backend with a deterministic embedding."""

    def make_store(self) -> Any:
        import tempfile
        from pathlib import Path

        from cela_sh.memory import SqliteVecMemoryStore

        temp_dir = tempfile.mkdtemp()
        db_path = Path(temp_dir) / "test_memory.db"

        return SqliteVecMemoryStore(
            db_path=str(db_path),
            embedding_provider=DeterministicEmbedding(),
            timeout=5.0,
        )


class QdrantSessionFilterDefenceTests(unittest.TestCase):
    """The adapter must not trust the server to filter sessions."""

    def test_adapter_still_filters_when_the_server_leaks_other_sessions(self):
        session = FakeQdrantSession()
        session.leak_other_sessions = True
        store = QdrantMemoryStore(
            collection_name="leak_test",
            embedding_provider=DeterministicEmbedding(),
            session=session,
        )
        store.add_memory("session-a", "alpha planning notes")
        store.add_memory("session-b", "beta storage details")

        results = store.search_memory("planning storage notes details", "session-a", limit=10)

        self.assertEqual([result["session_id"] for result in results], ["session-a"])
        self.assertNotIn("beta storage details", [result["content"] for result in results])


class ExtensionPointTests(unittest.TestCase):
    """Registering a backend is enough to make it usable end to end."""

    def setUp(self) -> None:
        register_memory_backend("example", _build_example_store)
        self.addCleanup(unregister_memory_backend, "example")

    def test_a_registered_backend_becomes_a_valid_setting_value(self):
        self.assertIn("example", memory_backend_names())
        self.assertTrue(is_known_memory_backend("EXAMPLE"))
        self.assertEqual(Settings(memory_backend="example").memory_backend, "example")

    def test_an_unregistered_backend_is_rejected_and_names_the_valid_ones(self):
        with self.assertRaises(ConfigurationError) as raised:
            Settings(memory_backend="redis")

        message = str(raised.exception)
        self.assertIn("'redis'", message)
        self.assertIn("'qdrant'", message)
        self.assertIn("'disabled'", message)

    def test_the_factory_injects_a_new_backend_without_being_edited(self):
        settings = Settings(memory_backend="example", qdrant_collection="example_space")

        planner = create_planner(settings)

        self.assertIsInstance(planner.memory_store, ExampleMemoryStore)
        self.assertEqual(planner.memory_store.namespace, "example_space")

    def test_a_new_backend_reaches_the_prompt_through_the_unchanged_planner(self):
        store = ExampleMemoryStore()
        store.add_memory("session-a", "Ollama planning notes from last run")
        planner = OllamaPlanner(memory_store=store, memory_limit=2)
        captured: dict[str, Any] = {}

        def stub_post(url, json=None, timeout=None, allow_redirects=True):
            captured["payload"] = json
            return mock.Mock(
                status_code=200,
                **{
                    "raise_for_status.return_value": None,
                    "json.return_value": {
                        "response": (
                            '{"analysis": "ok", "steps": [{"action": "OBSERVE", "target": "page",'
                            ' "value": "", "reason": "look"}]}'
                        )
                    },
                },
            )

        with mock.patch.object(planner._session, "post", side_effect=stub_post):
            plan = planner.generate_plan("Continue planning", session_id="session-a")

        self.assertEqual(plan.steps[0].action, Action.OBSERVE)
        self.assertIn("Ollama planning notes from last run", captured["payload"]["prompt"])
        self.assertEqual(store.added, [("session-a", "Ollama planning notes from last run")])

    def test_the_disabled_backend_never_builds_a_store(self):
        self.assertIsNone(create_planner(Settings()).memory_store)
        with self.assertRaises(ValueError):
            build_memory_store("disabled", Settings(), (3.05, 60.0))

    def test_registering_a_conflicting_backend_fails_loudly(self):
        with self.assertRaises(ValueError):
            register_memory_backend("example", lambda config, timeout: ExampleMemoryStore())
        register_memory_backend("example", _build_example_store)  # the same builder is a no-op
        with self.assertRaises(ValueError):
            register_memory_backend("disabled", _build_example_store)
        with self.assertRaises(TypeError):
            register_memory_backend("broken", "not callable")

    def test_a_builder_that_returns_a_non_store_is_rejected(self):
        register_memory_backend("broken", lambda config, timeout: {"not": "a store"}, replace=True)
        self.addCleanup(unregister_memory_backend, "broken")

        with self.assertRaises(TypeError):
            build_memory_store("broken", Settings(memory_backend="broken"), (3.05, 60.0))

    def test_an_unknown_backend_reports_the_known_names(self):
        with self.assertRaises(ConfigurationError) as raised:
            build_memory_store("redis", Settings(), (3.05, 60.0))
        self.assertIn("qdrant", str(raised.exception))

    def test_every_registered_backend_is_covered_by_a_contract_suite(self):
        uncovered = sorted(set(registered_memory_backends()) - COVERED_BACKENDS)
        self.assertEqual(
            uncovered,
            [],
            f"Add a MemoryStoreContract suite for: {uncovered}. A backend that skips the "
            "shared contract can silently break session isolation.",
        )


class SafetyInvariantTests(unittest.TestCase):
    """The properties that must survive every future extension."""

    def test_a_plan_can_never_report_execution_as_enabled(self):
        plan = Plan(**ready_plan_payload())
        dumped = plan.model_dump()

        self.assertTrue(dumped["dry_run"])
        self.assertFalse(dumped["execution_enabled"])
        for field, forbidden in (("dry_run", False), ("execution_enabled", True)):
            with self.subTest(field=field), self.assertRaises(ValidationError):
                Plan(**{**dumped, field: forbidden})

    def test_plans_reject_unknown_fields_so_the_schema_cannot_drift(self):
        payload = ready_plan_payload()
        Plan(**payload)  # sanity: the baseline payload is accepted

        with self.assertRaises(ValidationError) as raised:
            Plan(**payload, execution_log=[])

        self.assertIn("execution_log", str(raised.exception))

    def test_plan_models_refuse_to_coerce_untrusted_strings(self):
        with self.assertRaises(ValidationError):
            PlannedStep(**{**OBSERVE_STEP, "action": "RUN_SHELL"})

    def test_policy_never_returns_an_executable_plan(self):
        raw_plan = RawPlan(
            analysis="Install and pay.",
            steps=[
                RawStep(action=Action.OPEN_APP, target="terminal", value="", reason="Open a shell"),
                RawStep(action=Action.TYPE, target="field", value="rm -rf /", reason="Delete files"),
                RawStep(action=Action.STOP, target="", value="", reason="Finish"),
            ],
        )

        plan = apply_policy(raw_plan, PlannerInput(goal="install and pay"))

        self.assertTrue(plan.dry_run)
        self.assertFalse(plan.execution_enabled)
        self.assertEqual(plan.status, PlanStatus.BLOCKED)
        self.assertTrue(plan.requires_confirmation)
        self.assertIn(RiskLevel.BLOCKED, [step.risk for step in plan.steps])

    def test_planning_never_writes_to_memory(self):
        store = MockMemoryStore()
        planner = OllamaPlanner(memory_store=store)

        with mock.patch.object(store, "add_memory", wraps=store.add_memory) as add_memory:
            with mock.patch.object(planner._session, "post", side_effect=requests.ConnectionError):
                with self.assertRaises(Exception):
                    planner.generate_plan("a goal", session_id="session-a")

        add_memory.assert_not_called()
        self.assertEqual(store.search_memory("a goal", "session-a"), [])

    def test_ollama_stays_restricted_to_loopback(self):
        for base_url in ("http://10.0.0.5:11434", "https://127.0.0.1:11434", "http://user:pass@127.0.0.1:11434"):
            with self.subTest(base_url=base_url), self.assertRaises(ValueError):
                OllamaPlanner(base_url=base_url)

    def test_memory_requires_an_explicit_session_id(self):
        planner = OllamaPlanner(memory_store=MockMemoryStore())
        for session_id in (None, "   "):
            with self.subTest(session_id=session_id), self.assertRaises(Exception):
                planner.generate_plan("a goal", session_id=session_id)

    def test_settings_defaults_keep_memory_off_and_every_endpoint_local(self):
        settings = Settings()
        self.assertEqual(settings.memory_backend, "disabled")
        self.assertTrue(settings.ollama_url.startswith("http://127.0.0.1"))
        self.assertTrue(settings.qdrant_url.startswith("http://127.0.0.1"))


if __name__ == "__main__":
    unittest.main()
