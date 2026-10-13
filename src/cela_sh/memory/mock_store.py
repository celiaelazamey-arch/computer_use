import json
import re
from copy import deepcopy
from typing import Any

from .base import MemoryStore
from .metadata import metadata_matches, validate_memory_filters
from .sources import validate_source_replacement


def _tokens(text: str) -> set[str]:
    return set(re.findall(r"\w+", text.casefold(), flags=re.UNICODE))


class MockMemoryStore(MemoryStore):
    """Ephemeral lexical test double; it performs no embedding or persistence."""

    def __init__(self) -> None:
        self._storage: list[dict[str, Any]] = []

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

        self._storage.append(
            {
                "session_id": session_id.strip(),
                "content": content,
                "metadata": clean_metadata,
            }
        )
        return True

    def search_memory(self, query: str, session_id: str, limit: int = 3) -> list[dict[str, Any]]:
        if not query.strip():
            raise ValueError("query must not be blank")
        if not session_id.strip():
            raise ValueError("session_id must not be blank")
        if not isinstance(limit, int) or isinstance(limit, bool) or limit < 1:
            raise ValueError("limit must be a positive integer")

        query_tokens = _tokens(query)
        if not query_tokens:
            return []

        matches = []
        for index, memory in enumerate(self._storage):
            if memory["session_id"] != session_id.strip():
                continue
            overlap = query_tokens & _tokens(memory["content"])
            if overlap:
                matches.append(
                    (
                        len(overlap) / len(query_tokens),
                        index,
                        {
                            **memory,
                            "metadata": deepcopy(memory["metadata"]),
                            "score": len(overlap) / len(query_tokens),
                        },
                    )
                )

        matches.sort(key=lambda item: (item[0], item[1]), reverse=True)
        return [item[2] for item in matches[:limit]]

    def list_memory(
        self,
        session_id: str,
        limit: int = 100,
        *,
        tags: list[str] | None = None,
        memory_type: str | None = None,
    ) -> list[dict[str, Any]]:
        if not isinstance(session_id, str) or not session_id.strip():
            raise ValueError("session_id must not be blank")
        limit, clean_tags, clean_type = validate_memory_filters(limit, tags, memory_type)
        clean_session = session_id.strip()
        results = []
        for memory in reversed(self._storage):
            if memory["session_id"] != clean_session or not metadata_matches(
                memory["metadata"], tags=clean_tags, memory_type=clean_type
            ):
                continue
            results.append(
                {
                    **memory,
                    "metadata": deepcopy(memory["metadata"]),
                    "score": 0.0,
                }
            )
            if len(results) == limit:
                break
        return results

    def replace_source(
        self,
        session_id: str,
        source_path: str,
        source_hash: str,
        chunks: list[tuple[str, dict[str, Any]]],
    ) -> dict[str, Any]:
        """Replace imported source chunks while keeping the test double deterministic."""
        clean_session, clean_path, clean_hash, records = validate_source_replacement(
            session_id, source_path, source_hash, chunks
        )
        existing = [
            memory
            for memory in self._storage
            if memory["session_id"] == clean_session and memory["metadata"].get("source_path") == clean_path
        ]
        existing_by_index = {memory["metadata"].get("chunk_index"): memory for memory in existing}
        if len(existing_by_index) == len(existing) == len(records) and all(
            index in existing_by_index
            and existing_by_index[index]["metadata"].get("source_hash") == clean_hash
            and existing_by_index[index]["metadata"].get("content_hash") == metadata.get("content_hash")
            for index, (_, metadata) in enumerate(records, start=1)
        ):
            return {
                "status": "unchanged",
                "chunks_stored": 0,
                "replaced_chunks": 0,
                "existing_chunks": len(records),
            }

        self._storage = [
            memory
            for memory in self._storage
            if not (
                memory["session_id"] == clean_session
                and memory["metadata"].get("source_path") == clean_path
            )
        ]
        self._storage.extend(
            {
                "session_id": clean_session,
                "content": content,
                "metadata": deepcopy(metadata),
            }
            for content, metadata in records
        )
        return {
            "status": "updated" if existing else "imported",
            "chunks_stored": len(records),
            "replaced_chunks": len(existing),
            "existing_chunks": len(records),
        }
