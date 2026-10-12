"""Session-scoped exact vector search using the official sqlite-vec extension."""

import json
import math
import sqlite3
from contextlib import closing
from copy import deepcopy
from typing import Any
from uuid import uuid4
from weakref import finalize

import sqlite_vec

from .base import MemoryStore, MemoryStoreError
from .embeddings import EmbeddingProvider

# Limits in the pinned sqlite-vec release. A large requested limit still returns
# up to this many neighbours, never an invalid native KNN query.
_MAX_DIMENSIONS = 8192
_MAX_K = 4096


class SqliteVecMemoryStore(MemoryStore):
    """Local memory with native cosine KNN and session prefiltering in vec0.

    Text and JSON metadata live in ``memories``. Vectors live only in vec0
    virtual tables, one per embedding dimension. Both writes share a single
    transaction. Existing databases from the Python/BLOB implementation are
    migrated atomically, without calling the embedding provider again.
    """

    def __init__(
        self,
        db_path: str,
        embedding_provider: EmbeddingProvider,
        timeout: float = 5.0,
    ) -> None:
        if not isinstance(db_path, str) or not db_path.strip():
            raise ValueError("db_path must not be blank")
        if (
            isinstance(timeout, bool)
            or not isinstance(timeout, (int, float))
            or not math.isfinite(timeout)
            or timeout <= 0
        ):
            raise ValueError("timeout must be a positive finite number")

        if sqlite3.sqlite_version_info < (3, 35, 0):
            raise MemoryStoreError("sqlite-vec memory search requires SQLite 3.35 or newer.")

        self.db_path = db_path.strip()
        self.embedding_provider = embedding_provider
        self.timeout = float(timeout)
        self._connection_path = self.db_path
        self._uri = False

        # Ordinary :memory: connections do not share data. Keep an anchor for
        # this instance's private shared-memory database; operation connections
        # can then be short-lived, including when called from API worker threads.
        if self.db_path == ":memory:":
            self._connection_path = f"file:cela_memory_{uuid4().hex}?mode=memory&cache=shared"
            self._uri = True
            try:
                anchor = sqlite3.connect(
                    self._connection_path,
                    timeout=self.timeout,
                    uri=True,
                    check_same_thread=False,
                )
            except sqlite3.Error as exc:
                raise MemoryStoreError("Failed to open the SQLite memory database.") from exc
            self._anchor_finalizer = finalize(self, anchor.close)

        self._init_db()

    def _get_connection(self) -> sqlite3.Connection:
        """Load vec0 on every connection, then disable extension loading again."""
        conn = None
        try:
            conn = sqlite3.connect(self._connection_path, timeout=self.timeout, uri=self._uri)
            conn.row_factory = sqlite3.Row
            conn.enable_load_extension(True)
            try:
                sqlite_vec.load(conn)
            finally:
                conn.enable_load_extension(False)
            return conn
        except (sqlite3.Error, AttributeError, OSError) as exc:
            if conn is not None:
                conn.close()
            raise MemoryStoreError(
                "Could not open SQLite or load sqlite-vec. Install the project dependencies "
                "and use a SQLite build that supports loadable extensions."
            ) from exc

    @staticmethod
    def _create_memories_table(conn: sqlite3.Connection, table_name: str) -> None:
        # Only fixed, internal table names are supplied here.
        conn.execute(
            f"""
            CREATE TABLE {table_name} (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL,
                content TEXT NOT NULL,
                metadata TEXT NOT NULL DEFAULT '{{}}',
                embedding_dim INTEGER NOT NULL CHECK (embedding_dim BETWEEN 1 AND {_MAX_DIMENSIONS})
            )
            """
        )

    def _init_db(self) -> None:
        try:
            # sqlite3's own context manager commits/rolls back but does NOT
            # close the connection. closing() guarantees that both happen.
            with closing(self._get_connection()) as conn, conn:
                conn.execute("PRAGMA journal_mode = WAL")
                conn.execute("BEGIN IMMEDIATE")
                columns = {row["name"] for row in conn.execute("PRAGMA table_info(memories)")}
                if not columns:
                    self._create_memories_table(conn, "memories")
                elif "embedding" in columns:
                    self._migrate_legacy_memories(conn)
                elif not {"id", "session_id", "content", "metadata", "embedding_dim"} <= columns:
                    raise MemoryStoreError("The SQLite memory database has an unsupported schema.")
                conn.execute(
                    "CREATE INDEX IF NOT EXISTS idx_memories_session_id ON memories (session_id)"
                )
        except sqlite3.Error as exc:
            raise MemoryStoreError("Failed to initialize the sqlite-vec memory database.") from exc

    def _migrate_legacy_memories(self, conn: sqlite3.Connection) -> None:
        """Move legacy float32 BLOBs into vec0 in the initialization transaction."""
        self._create_memories_table(conn, "cela_memories_vec_migration")
        rows = conn.execute("SELECT id, session_id, content, metadata, embedding FROM memories")
        for row in rows:
            if not isinstance(row["embedding"], bytes):
                raise MemoryStoreError("A legacy embedding is not a float32 BLOB; migration was rolled back.")
            dimension = self._check_native_vector(conn, row["embedding"])
            table_name = self._ensure_vec_table(conn, dimension)
            conn.execute(
                """INSERT INTO cela_memories_vec_migration
                   (id, session_id, content, metadata, embedding_dim) VALUES (?, ?, ?, ?, ?)""",
                (row["id"], row["session_id"], row["content"], row["metadata"], dimension),
            )
            conn.execute(
                f"INSERT INTO {table_name} (rowid, session_id, embedding) VALUES (?, ?, ?)",
                (row["id"], row["session_id"], row["embedding"]),
            )
        conn.execute("DROP TABLE memories")
        conn.execute("ALTER TABLE cela_memories_vec_migration RENAME TO memories")

    @staticmethod
    def _check_native_vector(conn: sqlite3.Connection, vector: str | bytes) -> int:
        """Validate after float32 conversion, including zero/overflow/underflow.

        Cosine distance for an unusable vector is NULL/NaN in the extension. Do
        not persist it, or return a non-finite score to a caller. This also lets
        the extension decode legacy BLOBs without Python packing/unpacking.
        """
        row = conn.execute(
            "SELECT vec_length(?) AS dimension, vec_distance_cosine(?, ?) AS self_distance",
            (vector, vector, vector),
        ).fetchone()
        if not 1 <= row["dimension"] <= _MAX_DIMENSIONS:
            raise MemoryStoreError(f"Embedding dimensions must be between 1 and {_MAX_DIMENSIONS}.")
        distance = row["self_distance"]
        if distance is None or not math.isfinite(distance):
            raise MemoryStoreError("Embedding cannot produce a finite float32 cosine distance.")
        return row["dimension"]

    @staticmethod
    def _ensure_vec_table(conn: sqlite3.Connection, dimension: int) -> str:
        # dimension is an integer validated by _check_native_vector, not user
        # text. No commit here: DDL, metadata and vector insertion are atomic.
        # A filterable metadata column prefilters KNN without allocating a
        # separate, mostly empty vector chunk for every short-lived session.
        # Partition keys are better suited to hundreds of vectors per session.
        table_name = f"vec_memories_{dimension}"
        conn.execute(
            f"""CREATE VIRTUAL TABLE IF NOT EXISTS {table_name} USING vec0(
                embedding float[{dimension}] distance_metric=cosine,
                session_id text
            )"""
        )
        return table_name

    def _embed(self, text: str) -> list[float]:
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
        if len(vector) > _MAX_DIMENSIONS:
            raise MemoryStoreError(f"Embedding dimensions must be between 1 and {_MAX_DIMENSIONS}.")
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
        """Explicitly store one memory. Failures raise, never become False."""
        if not isinstance(session_id, str) or not session_id.strip():
            raise ValueError("session_id must not be blank")
        if not isinstance(content, str) or not content.strip() or len(content) > 8_000:
            raise ValueError("content must contain 1 to 8000 characters")
        if metadata is not None and not isinstance(metadata, dict):
            raise ValueError("metadata must be a dictionary")

        clean_metadata = deepcopy(metadata or {})
        try:
            meta_json = json.dumps(clean_metadata, ensure_ascii=False, allow_nan=False)
        except (TypeError, ValueError) as exc:
            raise ValueError("metadata must contain JSON-compatible values") from exc
        vector_json = json.dumps(self._embed(content), allow_nan=False)
        clean_session = session_id.strip()

        try:
            with closing(self._get_connection()) as conn, conn:
                conn.execute("BEGIN IMMEDIATE")
                dimension = self._check_native_vector(conn, vector_json)
                table_name = self._ensure_vec_table(conn, dimension)
                cursor = conn.execute(
                    "INSERT INTO memories (session_id, content, metadata, embedding_dim) VALUES (?, ?, ?, ?)",
                    (clean_session, content, meta_json, dimension),
                )
                conn.execute(
                    f"INSERT INTO {table_name} (rowid, session_id, embedding) VALUES (?, ?, ?)",
                    (cursor.lastrowid, clean_session, vector_json),
                )
        except sqlite3.Error as exc:
            raise MemoryStoreError("Failed to store memory in sqlite-vec.") from exc
        return True

    def search_memory(
        self, query: str, session_id: str, limit: int = 3
    ) -> list[dict[str, Any]]:
        """Find up to min(limit, 4096) neighbours in this session and dimension.

        Session filtering happens inside vec0's KNN operation, before top-k,
        and is checked again when joining metadata. Scores are cosine similarity
        (1 - native cosine distance), including negative similarities.
        """
        if not isinstance(query, str) or not query.strip():
            raise ValueError("query must not be blank")
        if not isinstance(session_id, str) or not session_id.strip():
            raise ValueError("session_id must not be blank")
        if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1:
            raise ValueError("limit must be a positive integer")

        vector_json = json.dumps(self._embed(query), allow_nan=False)
        clean_session = session_id.strip()
        try:
            with closing(self._get_connection()) as conn, conn:
                conn.execute("BEGIN")
                dimension = self._check_native_vector(conn, vector_json)
                table_name = f"vec_memories_{dimension}"
                exists = conn.execute(
                    "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (table_name,)
                ).fetchone()
                if exists is None:
                    return []
                rows = conn.execute(
                    f"""WITH nearest AS MATERIALIZED (
                        SELECT rowid, distance
                        FROM {table_name}
                        WHERE embedding MATCH ? AND k = ? AND session_id = ?
                        ORDER BY distance
                    )
                    SELECT m.session_id, m.content, m.metadata, nearest.distance
                    FROM nearest JOIN memories AS m ON m.id = nearest.rowid
                    WHERE m.session_id = ?
                    ORDER BY nearest.distance, m.id""",
                    (vector_json, min(limit, _MAX_K), clean_session, clean_session),
                ).fetchall()
        except sqlite3.Error as exc:
            raise MemoryStoreError("Failed to search memories in sqlite-vec.") from exc

        results = []
        for row in rows:
            distance = row["distance"]
            if distance is None or not math.isfinite(distance):
                raise MemoryStoreError("sqlite-vec returned a non-finite cosine distance.")
            try:
                metadata = json.loads(row["metadata"])
            except (TypeError, json.JSONDecodeError):
                metadata = {}
            if not isinstance(metadata, dict):
                metadata = {}
            results.append(
                {
                    "session_id": row["session_id"],
                    "content": row["content"],
                    "metadata": metadata,
                    "score": max(-1.0, min(1.0, 1.0 - float(distance))),
                }
            )
        return results
