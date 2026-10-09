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
