import ipaddress
import json
import math
from copy import deepcopy
from urllib.parse import quote, urlparse
from uuid import NAMESPACE_URL, uuid4, uuid5
from typing import Any

import requests

from .base import MemoryStore, MemoryStoreError
from .embeddings import EmbeddingProvider, OllamaEmbeddingProvider
from .metadata import metadata_matches, validate_memory_filters
from .sources import validate_source_replacement


class QdrantMemoryStore(MemoryStore):
    """Qdrant REST adapter with Ollama embeddings and mandatory session filtering."""

    def __init__(
        self,
        collection_name: str = "agent_memory",
        qdrant_url: str = "http://127.0.0.1:6333",
        embedding_provider: EmbeddingProvider | None = None,
        timeout: tuple[float, float] = (3.05, 15.0),
        api_key: str | None = None,
        session: requests.Session | None = None,
    ) -> None:
        if not collection_name or len(collection_name) > 255 or any(char not in "-_abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789" for char in collection_name):
            raise ValueError("collection_name contains unsupported characters")
        parsed = urlparse(qdrant_url)
        if parsed.scheme not in ("http", "https") or not parsed.hostname or parsed.username or parsed.password:
            raise ValueError("qdrant_url must be an HTTP(S) URL without embedded credentials")
        if parsed.path not in ("", "/") or parsed.query or parsed.fragment:
            raise ValueError("qdrant_url must not contain a path, query, or fragment")
        try:
            local_host = parsed.hostname == "localhost" or ipaddress.ip_address(parsed.hostname).is_loopback
        except ValueError:
            local_host = False
        if api_key is not None and not api_key.strip():
            raise ValueError("api_key must not be blank")
        if not local_host and (parsed.scheme != "https" or api_key is None):
            raise ValueError("remote Qdrant endpoints require HTTPS and an API key")

        self.collection_name = collection_name
        self.base_url = qdrant_url.rstrip("/")
        self.embedding_provider = embedding_provider or OllamaEmbeddingProvider()
        self.timeout = timeout
        self._collection_path = f"/collections/{quote(collection_name, safe='')}"
        self._vector_size: int | None = None
        # Injected transport seam: tests can supply an offline double instead of
        # patching a private attribute. Any object exposing request()/headers works.
        self._session = requests.Session() if session is None else session
        self._session.trust_env = False
        if api_key is not None:
            self._session.headers.update({"api-key": api_key})

    def _request(
        self,
        method: str,
        path: str,
        *,
        json_body: dict[str, Any] | None = None,
        allow_not_found: bool = False,
    ) -> dict[str, Any] | None:
        try:
            response = self._session.request(
                method,
                f"{self.base_url}{path}",
                json=json_body,
                timeout=self.timeout,
                allow_redirects=False,
            )
            if allow_not_found and response.status_code == 404:
                return None
            if 300 <= response.status_code < 400:
                raise MemoryStoreError("Qdrant redirects are not allowed.")
            response.raise_for_status()
        except requests.Timeout as exc:
            raise MemoryStoreError("Qdrant request timed out.") from exc
        except requests.HTTPError as exc:
            raise MemoryStoreError("Qdrant returned an unsuccessful HTTP status.") from exc
        except requests.RequestException as exc:
            raise MemoryStoreError("Could not contact Qdrant.") from exc

        try:
            body = response.json()
        except requests.JSONDecodeError as exc:
            raise MemoryStoreError("Qdrant returned invalid JSON.") from exc
        if not isinstance(body, dict) or body.get("status") == "error":
            raise MemoryStoreError("Qdrant returned an invalid response.")
        return body

    def _embedding(self, text: str) -> list[float]:
        try:
            vector = self.embedding_provider.embed(text)
        except MemoryStoreError:
            raise
        except Exception as exc:
            raise MemoryStoreError("Embedding provider failed.") from exc
        if not isinstance(vector, list) or not vector or any(
            isinstance(value, bool) or not isinstance(value, (int, float)) for value in vector
        ):
            raise MemoryStoreError("Embedding provider returned an invalid vector.")
        try:
            normalized = [float(value) for value in vector]
        except (OverflowError, ValueError) as exc:
            raise MemoryStoreError("Embedding provider returned an invalid vector.") from exc
        if any(not math.isfinite(value) for value in normalized):
            raise MemoryStoreError("Embedding provider returned an invalid vector.")
        return normalized

    def _ensure_collection(self, vector_size: int) -> None:
        if self._vector_size == vector_size:
            return

        body = self._request("GET", self._collection_path, allow_not_found=True)
        if body is None:
            self._request(
                "PUT",
                self._collection_path,
                json_body={"vectors": {"size": vector_size, "distance": "Cosine"}},
            )
        else:
            try:
                vectors = body["result"]["config"]["params"]["vectors"]
                existing_size = vectors["size"]
                distance = vectors["distance"]
            except (KeyError, TypeError) as exc:
                raise MemoryStoreError("Qdrant collection does not use a single unnamed vector.") from exc
            if (
                isinstance(existing_size, bool)
                or not isinstance(existing_size, int)
                or existing_size != vector_size
                or distance != "Cosine"
            ):
                raise MemoryStoreError("Qdrant collection vector size or distance does not match this adapter.")
        self._vector_size = vector_size

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

        vector = self._embedding(content)
        self._ensure_collection(len(vector))
        payload = {**clean_metadata, "session_id": session_id.strip(), "content": content}
        self._request(
            "PUT",
            f"{self._collection_path}/points?wait=true",
            json_body={"points": [{"id": str(uuid4()), "vector": vector, "payload": payload}]},
        )
        return True

    def search_memory(self, query: str, session_id: str, limit: int = 3) -> list[dict[str, Any]]:
        if not query.strip():
            raise ValueError("query must not be blank")
        if not session_id.strip():
            raise ValueError("session_id must not be blank")
        if not isinstance(limit, int) or isinstance(limit, bool) or limit < 1:
            raise ValueError("limit must be a positive integer")

        vector = self._embedding(query)
        self._ensure_collection(len(vector))
        body = self._request(
            "POST",
            f"{self._collection_path}/points/query",
            json_body={
                "query": vector,
                "filter": {"must": [{"key": "session_id", "match": {"value": session_id.strip()}}]},
                "limit": limit,
                "with_payload": True,
            },
        )

        try:
            points = body["result"]["points"]
        except (KeyError, TypeError) as exc:
            raise MemoryStoreError("Qdrant response did not contain query points.") from exc
        if not isinstance(points, list):
            raise MemoryStoreError("Qdrant returned an invalid point list.")

        results = []
        for point in points:
            payload = point.get("payload") if isinstance(point, dict) else None
            if not isinstance(payload, dict) or payload.get("session_id") != session_id.strip():
                continue
            content = payload.get("content")
            score = point.get("score")
            if (
                not isinstance(content, str)
                or isinstance(score, bool)
                or not isinstance(score, (int, float))
                or not math.isfinite(score)
            ):
                raise MemoryStoreError("Qdrant returned a malformed memory point.")
            if not content.strip() or len(content) > 8_000:
                raise MemoryStoreError("Qdrant returned content outside the accepted limits.")
            metadata = {
                key: deepcopy(value)
                for key, value in payload.items()
                if key not in {"session_id", "content"}
            }
            results.append(
                {
                    "session_id": session_id.strip(),
                    "content": content,
                    "metadata": metadata,
                    "score": float(score),
                }
            )
        return results

    def list_memory(
        self,
        session_id: str,
        limit: int = 100,
        *,
        tags: list[str] | None = None,
        memory_type: str | None = None,
    ) -> list[dict[str, Any]]:
        """Scroll one session with Qdrant-side metadata filters and local checks."""
        if not isinstance(session_id, str) or not session_id.strip():
            raise ValueError("session_id must not be blank")
        limit, clean_tags, clean_type = validate_memory_filters(limit, tags, memory_type)
        clean_session = session_id.strip()
        collection = self._request("GET", self._collection_path, allow_not_found=True)
        if collection is None:
            return []

        required = [{"key": "session_id", "match": {"value": clean_session}}]
        if clean_type is not None:
            required.append({"key": "type", "match": {"value": clean_type}})
        required.extend({"key": "tags", "match": {"value": tag}} for tag in clean_tags)
        body = self._request(
            "POST",
            f"{self._collection_path}/points/scroll",
            json_body={
                "filter": {"must": required},
                "limit": limit,
                "with_payload": True,
                "with_vector": False,
            },
        )
        try:
            points = body["result"]["points"]
        except (KeyError, TypeError) as exc:
            raise MemoryStoreError("Qdrant response did not contain scroll points.") from exc
        if not isinstance(points, list):
            raise MemoryStoreError("Qdrant returned an invalid scroll point list.")

        results = []
        for point in points:
            payload = point.get("payload") if isinstance(point, dict) else None
            if not isinstance(payload, dict) or payload.get("session_id") != clean_session:
                continue
            content = payload.get("content")
            if not isinstance(content, str) or not content.strip() or len(content) > 8_000:
                raise MemoryStoreError("Qdrant returned content outside the accepted limits.")
            metadata = {
                key: deepcopy(value)
                for key, value in payload.items()
                if key not in {"session_id", "content"}
            }
            if not metadata_matches(metadata, tags=clean_tags, memory_type=clean_type):
                continue
            results.append(
                {
                    "session_id": clean_session,
                    "content": content,
                    "metadata": metadata,
                    "score": 0.0,
                }
            )
        return results

    def _source_points(self, session_id: str, source_path: str) -> list[dict[str, Any]]:
        """Scroll all points for one source, following Qdrant pagination tokens."""
        collection = self._request("GET", self._collection_path, allow_not_found=True)
        if collection is None:
            return []

        points: list[dict[str, Any]] = []
        offset = None
        while True:
            body: dict[str, Any] = {
                "filter": {
                    "must": [
                        {"key": "session_id", "match": {"value": session_id}},
                        {"key": "source_path", "match": {"value": source_path}},
                    ]
                },
                "limit": 1_000,
                "with_payload": True,
                "with_vector": False,
            }
            if offset is not None:
                body["offset"] = offset
            response = self._request(
                "POST",
                f"{self._collection_path}/points/scroll",
                json_body=body,
            )
            try:
                result = response["result"]
                page = result["points"]
                next_offset = result.get("next_page_offset")
            except (KeyError, TypeError) as exc:
                raise MemoryStoreError("Qdrant response did not contain source points.") from exc
            if not isinstance(page, list):
                raise MemoryStoreError("Qdrant returned an invalid source point list.")
            for point in page:
                if not isinstance(point, dict):
                    raise MemoryStoreError("Qdrant returned a malformed source point.")
                payload = point.get("payload")
                if (
                    not isinstance(payload, dict)
                    or payload.get("session_id") != session_id
                    or payload.get("source_path") != source_path
                ):
                    continue
                if point.get("id") is None:
                    raise MemoryStoreError("Qdrant returned a source point without an ID.")
                points.append(point)
            if next_offset is None or next_offset == offset:
                break
            offset = next_offset
        return points

    @staticmethod
    def _source_is_unchanged(
        points: list[dict[str, Any]],
        source_hash: str,
        chunks: list[tuple[str, dict[str, Any]]],
    ) -> bool:
        if len(points) != len(chunks):
            return False
        by_index = {}
        for point in points:
            payload = point.get("payload")
            index = payload.get("chunk_index") if isinstance(payload, dict) else None
            if isinstance(index, bool) or not isinstance(index, int) or index in by_index:
                return False
            by_index[index] = payload
        for index, (content, metadata) in enumerate(chunks, start=1):
            payload = by_index.get(index)
            if (
                not isinstance(payload, dict)
                or payload.get("source_hash") != source_hash
                or payload.get("content_hash") != metadata.get("content_hash")
                or payload.get("content") != content
                or payload.get("chunk_count") != len(chunks)
            ):
                return False
        return True

    def replace_source(
        self,
        session_id: str,
        source_path: str,
        source_hash: str,
        chunks: list[tuple[str, dict[str, Any]]],
    ) -> dict[str, Any]:
        """Upsert a source's deterministic chunk IDs and remove its stale chunks."""
        clean_session, clean_path, clean_hash, records = validate_source_replacement(
            session_id, source_path, source_hash, chunks
        )
        old_points = self._source_points(clean_session, clean_path)
        if self._source_is_unchanged(old_points, clean_hash, records):
            return {
                "status": "unchanged",
                "chunks_stored": 0,
                "replaced_chunks": 0,
                "existing_chunks": len(records),
            }

        embedded = [(content, metadata, self._embedding(content)) for content, metadata in records]
        dimensions = {len(vector) for _, _, vector in embedded}
        if len(dimensions) != 1:
            raise MemoryStoreError("A single Qdrant source must use one consistent embedding dimension.")
        self._ensure_collection(dimensions.pop())

        point_ids: set[str] = set()
        points = []
        for index, (content, metadata, vector) in enumerate(embedded, start=1):
            identity = json.dumps(
                [clean_session, clean_path, index],
                ensure_ascii=False,
                separators=(",", ":"),
            )
            point_id = str(uuid5(NAMESPACE_URL, f"cela-source:{identity}"))
            point_ids.add(point_id)
            points.append(
                {
                    "id": point_id,
                    "vector": vector,
                    "payload": {**metadata, "session_id": clean_session, "content": content},
                }
            )

        self._request(
            "PUT",
            f"{self._collection_path}/points?wait=true",
            json_body={"points": points},
        )

        stale_ids = [point["id"] for point in old_points if point["id"] not in point_ids]
        if stale_ids:
            self._request(
                "POST",
                f"{self._collection_path}/points/delete?wait=true",
                json_body={"points": stale_ids},
            )

        return {
            "status": "updated" if old_points else "imported",
            "chunks_stored": len(records),
            "replaced_chunks": len(old_points),
            "existing_chunks": len(records),
        }
