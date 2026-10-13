from abc import ABC, abstractmethod
from typing import Any


class MemoryStoreError(RuntimeError):
    """Raised when a memory adapter cannot complete a storage operation."""


class MemoryStore(ABC):
    """Contract shared by in-memory and future persistent memory adapters."""

    @abstractmethod
    def add_memory(self, session_id: str, content: str, metadata: dict[str, Any] | None = None) -> bool:
        """Store one memory within a session scope."""
        raise NotImplementedError

    @abstractmethod
    def search_memory(self, query: str, session_id: str, limit: int = 3) -> list[dict[str, Any]]:
        """Return matching memories from one session only."""
        raise NotImplementedError

    def replace_source(
        self,
        session_id: str,
        source_path: str,
        source_hash: str,
        chunks: list[tuple[str, dict[str, Any]]],
    ) -> dict[str, Any]:
        """Atomically replace one imported source when supported by a backend."""
        raise MemoryStoreError("This memory backend does not support replace-by-source imports.")

    def list_memory(
        self,
        session_id: str,
        limit: int = 100,
        *,
        tags: list[str] | None = None,
        memory_type: str | None = None,
    ) -> list[dict[str, Any]]:
        """List scoped records using metadata filters when supported by a backend.

        This optional capability keeps existing third-party MemoryStore
        implementations compatible. Backends used with tag-only browsing must
        override it; semantic search remains available through search_memory.
        """
        raise MemoryStoreError("This memory backend does not support metadata listing.")
