"""Offline test doubles for the memory backends.

These let the shared contract suite run against the Qdrant adapter without a
server, a network, or a model. They are deliberately small: they implement only
the REST behaviour ``QdrantMemoryStore`` depends on.

Nothing here is collected by unittest discovery (the file does not match
``test*.py``); import it from a test module.
"""

import math
import re
from copy import deepcopy
from typing import Any
from urllib.parse import unquote, urlparse

import requests

from cela_sh.memory import EmbeddingProvider


class DeterministicEmbedding(EmbeddingProvider):
    """A tiny hashed bag-of-characters embedding.

    Deterministic, dependency-free, and lexically sensitive: texts that share
    tokens score higher. It is not semantically meaningful and exists only so
    offline tests can exercise ranking, limits, and session filtering.
    """

    def __init__(self, size: int = 16) -> None:
        if size < 2:
            raise ValueError("size must be at least 2")
        self.size = size
        self.inputs: list[str] = []

    def embed(self, text: str) -> list[float]:
        self.inputs.append(text)
        vector = [0.0] * self.size
        for token in re.findall(r"\w+", text.casefold(), flags=re.UNICODE):
            for char in token:
                vector[ord(char) % self.size] += 1.0
        norm = math.sqrt(sum(value * value for value in vector)) or 1.0
        return [value / norm for value in vector]


class FakeQdrantResponse:
    def __init__(self, body: dict[str, Any] | None = None, status_code: int = 200) -> None:
        self.body = body if body is not None else {}
        self.status_code = status_code

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise requests.HTTPError(f"HTTP {self.status_code}")

    def json(self) -> dict[str, Any]:
        return self.body


class FakeQdrantSession:
    """An in-memory stand-in for ``requests.Session`` that behaves like Qdrant.

    Supports the four calls the adapter makes: collection GET/PUT, points PUT,
    and points/query. Set :attr:`leak_other_sessions` to make ``points/query``
    ignore the session filter, which simulates a misconfigured or hostile server
    and proves the adapter's own filtering is not the only line of defence.
    """

    def __init__(self) -> None:
        self.headers: dict[str, str] = {}
        self.trust_env = True
        self.leak_other_sessions = False
        self.status_code_override: int | None = None
        self.collections: dict[str, dict[str, Any]] = {}
        self.calls: list[tuple[str, str, Any]] = []

    def request(
        self,
        method: str,
        url: str,
        json: dict[str, Any] | None = None,
        timeout: tuple[float, float] | None = None,
        allow_redirects: bool = True,
    ) -> FakeQdrantResponse:
        self.calls.append((method, url, deepcopy(json)))
        if self.status_code_override is not None:
            return FakeQdrantResponse({}, self.status_code_override)
        if allow_redirects is not False:
            raise AssertionError("QdrantMemoryStore must disable redirects")

        parsed = urlparse(url)
        parts = [part for part in unquote(parsed.path).split("/") if part]
        if len(parts) >= 2 and parts[0] == "collections":
            return self._collection_route(method, parts[1:], json or {})
        return FakeQdrantResponse({"status": "error", "result": None, "error": f"unknown route {parsed.path}"}, 404)

    def _collection_route(self, method: str, parts: list[str], body: dict[str, Any]) -> FakeQdrantResponse:
        name = parts[0]
        collection = self.collections.get(name)

        if len(parts) == 1:
            if method == "GET":
                if collection is None:
                    return FakeQdrantResponse({}, 404)
                return FakeQdrantResponse(
                    {
                        "status": "ok",
                        "result": {
                            "config": {
                                "params": {
                                    "vectors": {
                                        "size": collection["size"],
                                        "distance": collection["distance"],
                                    }
                                }
                            }
                        },
                    }
                )
            if method == "PUT":
                vectors = body.get("vectors", {})
                self.collections[name] = {
                    "size": vectors.get("size"),
                    "distance": vectors.get("distance"),
                    "points": [],
                }
                return FakeQdrantResponse({"status": "ok", "result": True})

        if len(parts) == 2 and parts[1] == "points" and method == "PUT":
            if collection is None:
                return FakeQdrantResponse({"status": "error", "error": "no such collection"}, 404)
            for point in body.get("points", []):
                collection["points"].append(deepcopy(point))
            return FakeQdrantResponse({"status": "ok", "result": {}})

        if len(parts) == 3 and parts[1] == "points" and parts[2] == "query" and method == "POST":
            if collection is None:
                return FakeQdrantResponse({"status": "error", "error": "no such collection"}, 404)
            return FakeQdrantResponse({"status": "ok", "result": {"points": self._query(collection, body)}})

        return FakeQdrantResponse({"status": "error", "error": "unsupported call"}, 400)

    def _query(self, collection: dict[str, Any], body: dict[str, Any]) -> list[dict[str, Any]]:
        query_vector = body.get("query") or []
        wanted = None
        for clause in (body.get("filter") or {}).get("must", []):
            if clause.get("key") == "session_id":
                wanted = clause.get("match", {}).get("value")

        scored = []
        for point in collection["points"]:
            payload = point.get("payload", {})
            if wanted is not None and not self.leak_other_sessions and payload.get("session_id") != wanted:
                continue
            scored.append({"payload": deepcopy(payload), "score": _cosine(query_vector, point.get("vector") or [])})
        scored.sort(key=lambda item: item["score"], reverse=True)
        limit = body.get("limit")
        return scored[:limit] if isinstance(limit, int) and limit > 0 else scored


def _cosine(left: list[float], right: list[float]) -> float:
    if not left or len(left) != len(right):
        return 0.0
    dot = sum(a * b for a, b in zip(left, right, strict=False))
    left_norm = math.sqrt(sum(a * a for a in left))
    right_norm = math.sqrt(sum(b * b for b in right))
    if not left_norm or not right_norm:
        return 0.0
    return dot / (left_norm * right_norm)
