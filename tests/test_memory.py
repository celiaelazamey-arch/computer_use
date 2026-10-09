import unittest

from cela_sh.memory import MemoryStore, MockMemoryStore


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

    def test_no_token_overlap_returns_empty_results(self):
        store = MockMemoryStore()
        store.add_memory("session-a", "Browser agent planning")
        self.assertEqual(store.search_memory("Qdrant persistence", "session-a"), [])


if __name__ == "__main__":
    unittest.main()
