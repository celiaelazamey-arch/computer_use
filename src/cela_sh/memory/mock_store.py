import json
import re
from copy import deepcopy
from typing import Any

from .base import MemoryStore


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
