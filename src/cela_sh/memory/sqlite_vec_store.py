import json
import math
import sqlite3
import struct
from copy import deepcopy
from typing import Any

from .base import MemoryStore, MemoryStoreError
from .embeddings import EmbeddingProvider


def _cosine_similarity(v1: list[float], v2: list[float]) -> float:
    """Calculate cosine similarity between two vectors."""
    dot_product = sum(a * b for a, b in zip(v1, v2))
    norm1 = math.sqrt(sum(a * a for a in v1))
    norm2 = math.sqrt(sum(b * b for b in v2))
    if norm1 == 0 or norm2 == 0:
        return 0.0
    return dot_product / (norm1 * norm2)


class SqliteVecMemoryStore(MemoryStore):
    """Local SQLite-backed memory store with vector embeddings for semantic search.

    Stores memories in a single SQLite file with:
    - session_id for isolation
    - content text (1-8000 chars)
    - metadata as JSON
    - embedding vector as BLOB (little-endian float32)

    Search performs cosine similarity on the fly. No external server required.
    """

    def __init__(
        self,
        db_path: str,
        embedding_provider: EmbeddingProvider,
        timeout: float = 5.0,
    ) -> None:
        if not db_path or not db_path.strip():
            raise ValueError("db_path must not be blank")
        if not isinstance(timeout, (int, float)) or not math.isfinite(timeout) or timeout <= 0:
            raise ValueError("timeout must be a positive finite number")

        self.db_path = db_path.strip()
        self.embedding_provider = embedding_provider
        self.timeout = float(timeout)
        self._init_db()

    def _get_connection(self) -> sqlite3.Connection:
        """Create a connection with row factory for dict-like access."""
        conn = sqlite3.connect(self.db_path, timeout=self.timeout)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        """Initialize the database schema."""
        with self._get_connection() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS memories (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT NOT NULL,
                    content TEXT NOT NULL,
                    metadata TEXT NOT NULL DEFAULT '{}',
                    embedding BLOB NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_memories_session_id
                ON memories (session_id)
                """
            )
            conn.commit()

    def _pack_vector(self, vector: list[float]) -> bytes:
        """Pack a list of floats into a BLOB (little-endian float32)."""
        return struct.pack(f"<{len(vector)}f", *vector)

    def _unpack_vector(self, blob: bytes) -> list[float]:
        """Unpack a BLOB into a list of floats."""
        if len(blob) % 4 != 0:
            raise MemoryStoreError("Stored embedding has invalid size")
        dim = len(blob) // 4
        return list(struct.unpack(f"<{dim}f", blob))

    def _embed(self, text: str) -> list[float]:
        """Generate embedding with validation."""
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

    def add_memory(
        self, session_id: str, content: str, metadata: dict[str, Any] | None = None
    ) -> bool:
        """Store a memory within a session scope.

        Args:
            session_id: Non-blank session identifier.
            content: Text content (1-8000 characters).
            metadata: Optional JSON-serializable dictionary.

        Returns:
            True on success.

        Raises:
            ValueError: If input validation fails.
            MemoryStoreError: If embedding or storage fails.
        """
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

        vector = self._embed(content)
        vector_blob = self._pack_vector(vector)
        meta_json = json.dumps(clean_metadata, ensure_ascii=False, allow_nan=False)

        try:
            with self._get_connection() as conn:
                conn.execute(
                    "INSERT INTO memories (session_id, content, metadata, embedding) VALUES (?, ?, ?, ?)",
                    (session_id.strip(), content, meta_json, vector_blob),
                )
                conn.commit()
        except sqlite3.Error as exc:
            raise MemoryStoreError("Failed to store memory in SQLite.") from exc

        return True

    def search_memory(
        self, query: str, session_id: str, limit: int = 3
    ) -> list[dict[str, Any]]:
        """Return matching memories from one session only, ranked by cosine similarity.

        Args:
            query: Search query (non-blank).
            session_id: Session to search within (non-blank).
            limit: Maximum results to return (positive integer).

        Returns:
            List of dicts with keys: session_id, content, metadata, score.

        Raises:
            ValueError: If input validation fails.
            MemoryStoreError: If embedding or search fails.
        """
        if not query.strip():
            raise ValueError("query must not be blank")
        if not session_id.strip():
            raise ValueError("session_id must not be blank")
        if not isinstance(limit, int) or isinstance(limit, bool) or limit < 1:
            raise ValueError("limit must be a positive integer")

        query_vector = self._embed(query)

        try:
            with self._get_connection() as conn:
                cursor = conn.execute(
                    "SELECT id, session_id, content, metadata, embedding FROM memories WHERE session_id = ?",
                    (session_id.strip(),),
                )
                rows = cursor.fetchall()
        except sqlite3.Error as exc:
            raise MemoryStoreError("Failed to search memories in SQLite.") from exc

        if not rows:
            return []

        scored_results = []
        for row in rows:
            stored_vector = self._unpack_vector(row["embedding"])
            score = _cosine_similarity(query_vector, stored_vector)

            metadata = {}
            if row["metadata"]:
                try:
                    metadata = json.loads(row["metadata"])
                except json.JSONDecodeError:
                    metadata = {}

            scored_results.append(
                {
                    "session_id": row["session_id"],
                    "content": row["content"],
                    "metadata": deepcopy(metadata),
                    "score": score,
                }
            )

        scored_results.sort(key=lambda x: x["score"], reverse=True)
        return scored_results[:limit]