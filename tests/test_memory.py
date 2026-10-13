import hashlib
import unittest

from cela_sh.memory import MemoryStore, MockMemoryStore
from cela_sh.memory.sources import content_hash
from cela_sh.memory.scopes import GLOBAL_MEMORY_SESSION_ID, memory_session_id, search_session_ids


class MemoryStoreTests(unittest.TestCase):
    def test_abstract_contract_rejects_incomplete_adapters(self):
        class IncompleteStore(MemoryStore):
            pass

        with self.assertRaises(TypeError):
            IncompleteStore()

    def test_search_is_lexical_ranked_and_session_scoped(self):
        store = MockMemoryStore()
        store.add_memory("session-a", "Ollama local embedding for agent planning")
        store.add_memory("session-a", "Agent planning notes")
        store.add_memory("session-b", "Ollama embedding secret")

        results = store.search_memory("Ollama embedding", session_id="session-a", limit=5)

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["content"], "Ollama local embedding for agent planning")
        self.assertEqual(results[0]["session_id"], "session-a")
        self.assertGreater(results[0]["score"], 0)

    def test_metadata_is_copied_and_limit_must_be_positive(self):
        store = MockMemoryStore()
        metadata = {"source": {"kind": "test"}}
        store.add_memory("session-a", "Arabic ذاكرة محلية", metadata)
        metadata["source"]["kind"] = "mutated"

        result = store.search_memory("ذاكرة محلية", "session-a", limit=1)[0]
        self.assertEqual(result["metadata"]["source"]["kind"], "test")
        result["metadata"]["source"]["kind"] = "changed result"
        self.assertEqual(
            store.search_memory("ذاكرة محلية", "session-a", limit=1)[0]["metadata"]["source"]["kind"],
            "test",
        )
        with self.assertRaises(ValueError):
            store.search_memory("ذاكرة", "session-a", limit=0)

    def test_list_memory_filters_all_tags_and_type_without_embedding(self):
        store = MockMemoryStore()
        store.add_memory(
            "project-a",
            "Never use production in tests.",
            {"type": "correction", "tags": ["testing", "database"], "scope": "project"},
        )
        store.add_memory("project-a", "Deployment checklist", {"type": "note", "tags": ["deployment"]})
        store.add_memory("project-b", "Other project correction", {"type": "correction", "tags": ["testing"]})

        results = store.list_memory("project-a", tags=["TESTING", "database"], memory_type="correction")

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["content"], "Never use production in tests.")
        self.assertEqual(results[0]["score"], 0.0)
        self.assertEqual(store.list_memory("project-a", tags=["deployment"]), [
            {
                "session_id": "project-a",
                "content": "Deployment checklist",
                "metadata": {"type": "note", "tags": ["deployment"]},
                "score": 0.0,
            }
        ])

    def test_global_scope_mapping_is_reserved_and_both_searches_project_plus_global(self):
        self.assertEqual(memory_session_id("global"), GLOBAL_MEMORY_SESSION_ID)
        self.assertEqual(memory_session_id("project", "project-a"), "project-a")
        self.assertEqual(
            search_session_ids("both", "project-a"),
            [("project", "project-a"), ("global", GLOBAL_MEMORY_SESSION_ID)],
        )
        for scope, session_id in (("project", None), ("project", GLOBAL_MEMORY_SESSION_ID), ("global", "project-a")):
            with self.subTest(scope=scope, session_id=session_id), self.assertRaises(ValueError):
                memory_session_id(scope, session_id)

    def test_mock_replace_source_is_idempotent_and_replaces_old_chunks(self):
        store = MockMemoryStore()
        path = "docs/guide.md"

        def records(source_hash, contents):
            return [
                (
                    content,
                    {
                        "scope": "project",
                        "type": "note",
                        "tags": ["guide"],
                        "source_path": path,
                        "source_hash": source_hash,
                        "content_hash": content_hash(content),
                        "imported_at": "2026-10-12T12:00:00+00:00",
                        "chunk_index": index,
                        "chunk_count": len(contents),
                    },
                )
                for index, content in enumerate(contents, start=1)
            ]

        first_hash = hashlib.sha256(b"source-v1").hexdigest()
        first = store.replace_source("project-a", path, first_hash, records(first_hash, ["old fact", "stale fact"]))
        self.assertEqual(first["status"], "imported")
        unchanged = store.replace_source("project-a", path, first_hash, records(first_hash, ["old fact", "stale fact"]))
        self.assertEqual(unchanged["status"], "unchanged")

        second_hash = hashlib.sha256(b"source-v2").hexdigest()
        updated = store.replace_source("project-a", path, second_hash, records(second_hash, ["current fact"]))
        self.assertEqual(updated["status"], "updated")
        self.assertEqual(updated["replaced_chunks"], 2)
        self.assertEqual([item["content"] for item in store.list_memory("project-a")], ["current fact"])

    def test_no_token_overlap_returns_empty_results(self):
        store = MockMemoryStore()
        store.add_memory("session-a", "Browser agent planning")
        self.assertEqual(store.search_memory("Qdrant persistence", "session-a"), [])


if __name__ == "__main__":
    unittest.main()
