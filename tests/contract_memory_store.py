"""The behavioural contract every memory backend must satisfy.

Subclassing :class:`~cela_sh.memory.MemoryStore` only proves a backend has the
right method names. This suite proves it behaves safely: it validates input,
never leaks another session's memories, returns the documented result shape, and
hands back copies rather than shared mutable state.

To hold a new backend to the same rules, add this to a test module::

    class ExampleStoreContractTests(MemoryStoreContract, unittest.TestCase):
        def make_store(self):
            return ExampleStore()

The class is a plain mixin, not a ``TestCase``, so unittest discovery never runs
it on its own and a backend cannot opt out of a check by forgetting to inherit.
"""

import math
from typing import Any

from cela_sh.memory import satisfies_memory_contract

RESULT_KEYS = {"session_id", "content", "metadata", "score"}


class MemoryStoreContract:
    """Guarantees that are identical for every backend in the repository."""

    #: A backend that cannot validate input locally may set this to False, but
    #: it must then document why. Session isolation is never optional.
    enforces_input_limits = True

    def make_store(self) -> Any:
        """Return a fresh, isolated store. Called once per test."""
        raise NotImplementedError("a contract test must provide make_store()")

    # -- identity -----------------------------------------------------------

    def test_satisfies_the_memory_store_contract(self):
        self.assertTrue(
            satisfies_memory_contract(self.make_store()),
            "a backend must expose add_memory and search_memory",
        )

    # -- add_memory validation ---------------------------------------------

    def test_add_rejects_blank_session_id(self):
        store = self.make_store()
        for session_id in ("", "   "):
            with self.subTest(session_id=session_id), self.assertRaises(ValueError):
                store.add_memory(session_id, "usable content")

    def test_add_rejects_blank_or_oversized_content(self):
        if not self.enforces_input_limits:
            self.skipTest("backend documents that it cannot validate content length locally")
        store = self.make_store()
        for content in ("", "   ", "x" * 8_001):
            with self.subTest(length=len(content)), self.assertRaises(ValueError):
                store.add_memory("session-a", content)

    def test_add_rejects_non_dict_metadata(self):
        store = self.make_store()
        for metadata in ("text", 7, ["planning"]):
            with self.subTest(metadata=type(metadata).__name__), self.assertRaises(ValueError):
                store.add_memory("session-a", "usable content", metadata)

    def test_add_rejects_metadata_that_is_not_json_serialisable(self):
        store = self.make_store()
        for metadata in ({"when": object()}, {"rate": float("nan")}):
            with self.subTest(metadata=sorted(metadata)), self.assertRaises(ValueError):
                store.add_memory("session-a", "usable content", metadata)

    def test_add_accepts_unicode_content_and_reports_success(self):
        store = self.make_store()
        self.assertTrue(store.add_memory("session-a", "ذاكرة عربية Ollama planning"))

    def test_metadata_is_copied_on_write(self):
        store = self.make_store()
        metadata = {"topic": {"kind": "planning"}}
        store.add_memory("session-a", "Ollama planning notes", metadata)
        metadata["topic"]["kind"] = "tampered after the call"

        result = store.search_memory("Ollama planning", "session-a", limit=1)[0]
        self.assertEqual(result["metadata"]["topic"]["kind"], "planning")

    # -- search_memory validation ------------------------------------------

    def test_search_rejects_blank_query_and_blank_session(self):
        store = self.make_store()
        with self.assertRaises(ValueError):
            store.search_memory("   ", "session-a")
        with self.assertRaises(ValueError):
            store.search_memory("Ollama planning", "  ")

    def test_search_rejects_non_positive_or_boolean_limit(self):
        store = self.make_store()
        for limit in (0, -1, True, False):
            with self.subTest(limit=limit), self.assertRaises(ValueError):
                store.search_memory("Ollama planning", "session-a", limit=limit)

    # -- the security-critical guarantees ----------------------------------

    def test_memories_from_other_sessions_are_never_returned(self):
        store = self.make_store()
        store.add_memory("session-a", "alpha planning notes")
        store.add_memory("session-b", "beta storage details")

        for query in ("alpha planning notes", "beta storage details", "planning", "storage"):
            with self.subTest(query=query):
                results = store.search_memory(query, "session-a", limit=10)
                for result in results:
                    self.assertEqual(result["session_id"], "session-a")
                    self.assertEqual(result["content"], "alpha planning notes")

    def test_an_unknown_session_returns_nothing(self):
        store = self.make_store()
        store.add_memory("session-a", "Ollama planning notes")
        self.assertEqual(store.search_memory("Ollama planning", "session-unknown", limit=5), [])

    def test_returns_matching_memories_from_the_requested_session(self):
        store = self.make_store()
        store.add_memory("session-a", "Ollama local planning notes")

        results = store.search_memory("Ollama planning", "session-a", limit=5)

        self.assertTrue(results, "a matching memory in the same session must be returned")
        self.assertEqual(results[0]["content"], "Ollama local planning notes")

    def test_limit_caps_the_number_of_results(self):
        store = self.make_store()
        for index in range(5):
            store.add_memory("session-a", f"Ollama planning note {index}")

        results = store.search_memory("Ollama planning", "session-a", limit=2)

        self.assertLessEqual(len(results), 2)

    # -- result shape and isolation ----------------------------------------

    def test_results_have_the_documented_shape(self):
        store = self.make_store()
        store.add_memory("session-a", "Ollama planning", {"topic": "memory"})

        result = store.search_memory("Ollama planning", "session-a", limit=1)[0]

        self.assertEqual(set(result), RESULT_KEYS)
        self.assertIsInstance(result["session_id"], str)
        self.assertIsInstance(result["content"], str)
        self.assertIsInstance(result["metadata"], dict)
        self.assertIsInstance(result["score"], float)
        self.assertTrue(math.isfinite(result["score"]))

    def test_returned_results_are_copies_not_shared_state(self):
        store = self.make_store()
        store.add_memory("session-a", "Ollama planning", {"topic": "memory"})

        first = store.search_memory("Ollama planning", "session-a", limit=1)[0]
        first["content"] = "tampered"
        first["metadata"]["topic"] = "tampered"

        second = store.search_memory("Ollama planning", "session-a", limit=1)[0]
        self.assertEqual(second["content"], "Ollama planning")
        self.assertEqual(second["metadata"]["topic"], "memory")

    def test_an_empty_store_returns_no_results_without_error(self):
        self.assertEqual(self.make_store().search_memory("Ollama planning", "session-a"), [])
