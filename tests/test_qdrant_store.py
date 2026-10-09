import unittest
from unittest.mock import Mock, patch

import requests

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
