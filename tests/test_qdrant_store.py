import unittest
from hashlib import sha256
from unittest.mock import Mock, patch

import requests

from fakes import FakeQdrantSession

from cela_sh.memory import (
    EmbeddingProvider,
    MemoryStoreError,
    OllamaEmbeddingProvider,
    QdrantMemoryStore,
)


class FakeResponse:
    def __init__(self, body=None, status_code=200):
        self.body = body or {}
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError("HTTP error")

    def json(self):
        return self.body


class FixedEmbedding(EmbeddingProvider):
    def __init__(self, vector=None, error=None):
        self.vector = vector or [0.1, 0.2, 0.3]
        self.error = error
        self.inputs = []

    def embed(self, text):
        self.inputs.append(text)
        if self.error:
            raise self.error
        return self.vector


def collection_response(size=3):
    return FakeResponse(
        {
            "status": "ok",
            "result": {"config": {"params": {"vectors": {"size": size, "distance": "Cosine"}}}},
        }
    )


class OllamaEmbeddingTests(unittest.TestCase):
    def test_uses_local_embed_endpoint_and_validates_vector(self):
        provider = OllamaEmbeddingProvider()
        response = FakeResponse({"embeddings": [[0.25, -0.5, 1]]})
        with patch.object(provider._session, "post", return_value=response) as post:
            vector = provider.embed("local memory")

        self.assertEqual(vector, [0.25, -0.5, 1.0])
        self.assertEqual(post.call_args.args[0], "http://127.0.0.1:11434/api/embed")
        self.assertEqual(post.call_args.kwargs["json"], {"model": "nomic-embed-text", "input": "local memory"})
        self.assertFalse(post.call_args.kwargs["allow_redirects"])
        self.assertFalse(provider._session.trust_env)

    def test_rejects_bad_embedding_response_and_redirects(self):
        provider = OllamaEmbeddingProvider()
        with patch.object(provider._session, "post", return_value=FakeResponse({"embeddings": []})):
            with self.assertRaises(MemoryStoreError):
                provider.embed("memory")
        with patch.object(provider._session, "post", return_value=FakeResponse({}, status_code=302)):
            with self.assertRaises(MemoryStoreError):
                provider.embed("memory")

    def test_timeout_is_wrapped_and_remote_http_ollama_is_rejected(self):
        provider = OllamaEmbeddingProvider()
        with patch.object(provider._session, "post", side_effect=requests.Timeout):
            with self.assertRaises(MemoryStoreError):
                provider.embed("memory")
        with self.assertRaises(ValueError):
            OllamaEmbeddingProvider(base_url="http://10.0.0.5:11434")


class QdrantStoreTests(unittest.TestCase):
    def test_add_creates_collection_and_stores_isolated_payload(self):
        embedder = FixedEmbedding()
        store = QdrantMemoryStore(collection_name="test_memory", embedding_provider=embedder)
        responses = [FakeResponse(status_code=404), FakeResponse({"status": "ok"}), FakeResponse({"status": "ok"})]
        metadata = {"topic": "planning", "session_id": "wrong-session", "content": "wrong-content"}

        with patch.object(store._session, "request", side_effect=responses) as request:
            self.assertTrue(store.add_memory("session-a", "agent planning worked", metadata))

        self.assertEqual(len(request.call_args_list), 3)
        self.assertEqual(request.call_args_list[0].args[:2], ("GET", "http://127.0.0.1:6333/collections/test_memory"))
        self.assertEqual(
            request.call_args_list[1].kwargs["json"],
            {"vectors": {"size": 3, "distance": "Cosine"}},
        )
        point = request.call_args_list[2].kwargs["json"]["points"][0]
        self.assertEqual(point["vector"], [0.1, 0.2, 0.3])
        self.assertEqual(
            point["payload"],
            {"topic": "planning", "session_id": "session-a", "content": "agent planning worked"},
        )
        self.assertEqual(
            metadata,
            {"topic": "planning", "session_id": "wrong-session", "content": "wrong-content"},
        )
        self.assertTrue(request.call_args.kwargs["allow_redirects"] is False)

    def test_search_sends_mandatory_session_filter_and_rechecks_results(self):
        embedder = FixedEmbedding()
        store = QdrantMemoryStore(embedding_provider=embedder, api_key="local-test-key")
        points = FakeResponse(
            {
                "status": "ok",
                "result": {
                    "points": [
                        {"payload": {"session_id": "session-a", "content": "match", "tag": "safe"}, "score": 0.8},
                        {"payload": {"session_id": "session-b", "content": "must not leak"}, "score": 0.99},
                    ]
                },
            }
        )
        with patch.object(store._session, "request", side_effect=[collection_response(), points]) as request:
            results = store.search_memory("agent goal", "session-a", limit=2)

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["content"], "match")
        self.assertEqual(results[0]["metadata"], {"tag": "safe"})
        query_body = request.call_args.kwargs["json"]
        self.assertEqual(
            query_body["filter"],
            {"must": [{"key": "session_id", "match": {"value": "session-a"}}]},
        )
        self.assertEqual(query_body["limit"], 2)
        self.assertEqual(store._session.headers["api-key"], "local-test-key")

    def test_list_uses_scoped_type_and_tag_filters_and_rechecks_payload(self):
        store = QdrantMemoryStore(embedding_provider=FixedEmbedding())
        scroll_response = FakeResponse(
            {
                "status": "ok",
                "result": {
                    "points": [
                        {
                            "payload": {
                                "session_id": "session-a",
                                "content": "Never use production in tests.",
                                "type": "correction",
                                "tags": ["testing", "database"],
                                "scope": "project",
                            }
                        },
                        {
                            "payload": {
                                "session_id": "session-b",
                                "content": "must not leak",
                                "type": "correction",
                                "tags": ["testing", "database"],
                            }
                        },
                    ]
                },
            }
        )
        responses = [
            FakeResponse(status_code=404),
            FakeResponse({"status": "ok"}),
            FakeResponse({"status": "ok"}),
            FakeResponse({"status": "ok"}),
            FakeResponse({"status": "ok"}),
            collection_response(),
            scroll_response,
        ]
        with patch.object(store._session, "request", side_effect=responses) as request:
            store.add_memory(
                "session-a",
                "Never use production in tests.",
                {"type": "correction", "tags": ["testing", "database"], "scope": "project"},
            )
            store.add_memory("session-a", "Deployment checklist", {"type": "note", "tags": ["deployment"]})
            store.add_memory("session-b", "Other project", {"type": "correction", "tags": ["testing", "database"]})
            results = store.list_memory(
                "session-a", limit=10, tags=["testing", "database"], memory_type="correction"
            )

        self.assertEqual([result["content"] for result in results], ["Never use production in tests."])
        self.assertEqual(results[0]["score"], 0.0)
        scroll_body = request.call_args.kwargs["json"]
        self.assertEqual(
            scroll_body["filter"]["must"],
            [
                {"key": "session_id", "match": {"value": "session-a"}},
                {"key": "type", "match": {"value": "correction"}},
                {"key": "tags", "match": {"value": "database"}},
                {"key": "tags", "match": {"value": "testing"}},
            ],
        )

    def test_replace_source_skips_unchanged_files_and_removes_stale_chunks(self):
        embedding = FixedEmbedding()
        session = FakeQdrantSession()
        store = QdrantMemoryStore(embedding_provider=embedding, session=session)
        source_path = "docs/guide.md"

        def chunks(contents, source_hash, imported_at, path=source_path):
            return [
                (
                    content,
                    {
                        "scope": "project",
                        "type": "note",
                        "tags": ["guide"],
                        "source": "explicit_cli_file_learning",
                        "source_name": path.rsplit("/", 1)[-1],
                        "source_path": path,
                        "source_hash": source_hash,
                        "source_sha256": source_hash,
                        "content_hash": sha256(content.encode("utf-8")).hexdigest(),
                        "imported_at": imported_at,
                        "chunk_index": index,
                        "chunk_count": len(contents),
                    },
                )
                for index, content in enumerate(contents, start=1)
            ]

        first_hash = sha256(b"guide-v1").hexdigest()
        result = store.replace_source(
            "project-a", source_path, first_hash, chunks(["first", "second", "third"], first_hash, "2026-10-12T00:00:00+00:00")
        )
        self.assertEqual(result["status"], "imported")
        self.assertEqual(result["chunks_stored"], 3)
        self.assertEqual(len(session.collections["agent_memory"]["points"]), 3)
        embed_count = len(embedding.inputs)

        unchanged = store.replace_source(
            "project-a", source_path, first_hash, chunks(["first", "second", "third"], first_hash, "2026-10-13T00:00:00+00:00")
        )
        self.assertEqual(unchanged["status"], "unchanged")
        self.assertEqual(len(embedding.inputs), embed_count)

        other_path = "docs/other.md"
        other_hash = sha256(b"other guide").hexdigest()
        store.replace_source(
            "project-a",
            other_path,
            other_hash,
            chunks(["independent source"], other_hash, "2026-10-13T00:00:00+00:00", path=other_path),
        )
        second_hash = sha256(b"guide-v2").hexdigest()
        updated = store.replace_source(
            "project-a", source_path, second_hash, chunks(["updated first", "updated second"], second_hash, "2026-10-14T00:00:00+00:00")
        )
        self.assertEqual(updated["status"], "updated")
        self.assertEqual(updated["replaced_chunks"], 3)
        points = session.collections["agent_memory"]["points"]
        self.assertEqual(len(points), 3)
        target_points = [point for point in points if point["payload"]["source_path"] == source_path]
        self.assertEqual({point["payload"]["content"] for point in target_points}, {"updated first", "updated second"})
        self.assertEqual({point["payload"]["source_hash"] for point in target_points}, {second_hash})
        self.assertEqual(
            [point["payload"]["content"] for point in points if point["payload"]["source_path"] == other_path],
            ["independent source"],
        )

    def test_rejects_dimension_mismatch_and_maps_qdrant_errors(self):
        store = QdrantMemoryStore(embedding_provider=FixedEmbedding())
        with patch.object(store._session, "request", return_value=collection_response(size=2)):
            with self.assertRaises(MemoryStoreError):
                store.search_memory("query", "session-a")

        store = QdrantMemoryStore(embedding_provider=FixedEmbedding())
        with patch.object(store._session, "request", return_value=FakeResponse(status_code=503)):
            with self.assertRaises(MemoryStoreError):
                store.search_memory("query", "session-a")

    def test_rejects_insecure_remote_endpoint_and_redirects(self):
        with self.assertRaises(ValueError):
            QdrantMemoryStore(qdrant_url="http://10.0.0.4:6333", embedding_provider=FixedEmbedding())
        with self.assertRaises(ValueError):
            QdrantMemoryStore(qdrant_url="https://qdrant.example:6333", embedding_provider=FixedEmbedding())

        remote = QdrantMemoryStore(
            qdrant_url="https://qdrant.example:6333",
            api_key="test-key",
            embedding_provider=FixedEmbedding(),
        )
        self.assertEqual(remote._session.headers["api-key"], "test-key")

        store = QdrantMemoryStore(embedding_provider=FixedEmbedding())
        with patch.object(store._session, "request", return_value=FakeResponse(status_code=302)):
            with self.assertRaises(MemoryStoreError):
                store.search_memory("query", "session-a")


if __name__ == "__main__":
    unittest.main()
