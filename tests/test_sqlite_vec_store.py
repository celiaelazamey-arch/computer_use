"""Exercise the real, locally loaded sqlite-vec extension, never a fake index."""

import json
import sqlite3
import struct
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from cela_sh.memory import EmbeddingProvider, MemoryStoreError, SqliteVecMemoryStore


class VectorEmbedding(EmbeddingProvider):
    def __init__(self, vectors=None):
        self.vectors = vectors or {}
        self.inputs = []
        self.default = [1.0, 0.0]

    def embed(self, text):
        # The real provider contract takes one string, not a list of strings.
        if not isinstance(text, str):
            raise AssertionError("embed requires a string")
        self.inputs.append(text)
        return self.vectors.get(text, self.default)


class TrackingConnection(sqlite3.Connection):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.loading_flags = []
        self.closed = False

    def enable_load_extension(self, enabled):
        self.loading_flags.append(enabled)
        return super().enable_load_extension(enabled)

    def close(self):
        self.closed = True
        super().close()


class SqliteVecStoreTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path = str(Path(directory.name) / "memory.db")
        self.embedding = VectorEmbedding()

    def make_store(self, path=None):
        return SqliteVecMemoryStore(path or self.path, self.embedding)

    def test_real_vec0_schema_and_native_cosine_ranking(self):
        self.embedding.vectors = {
            "exact": [10.0, 0.0],
            "angled": [1.0, 1.0],
            "orthogonal": [0.0, 1.0],
            "opposite": [-1.0, 0.0],
        }
        store = self.make_store()
        for content in self.embedding.vectors:
            store.add_memory("session-a", content)
        results = store.search_memory("query", "session-a", limit=10)

        self.assertEqual([item["content"] for item in results], list(self.embedding.vectors))
        for item, score in zip(results, (1.0, 2**-0.5, 0.0, -1.0), strict=True):
            self.assertAlmostEqual(item["score"], score, places=6)
            self.assertEqual(set(item), {"session_id", "content", "metadata", "score"})
        with closing(store._get_connection()) as conn:
            self.assertEqual(conn.execute("SELECT vec_version()").fetchone()[0], "v0.1.9")
            schema = conn.execute(
                "SELECT sql FROM sqlite_master WHERE name = 'vec_memories_2'"
            ).fetchone()[0]
            self.assertIn("USING vec0", schema)
            self.assertIn("distance_metric=cosine", schema)
            self.assertIn("session_id text", schema)
            self.assertNotIn("partition key", schema)
            columns = {row["name"] for row in conn.execute("PRAGMA table_info(memories)")}
            self.assertNotIn("embedding", columns)
            self.assertIn("embedding_dim", columns)
            self.assertEqual(conn.execute("SELECT count(*) FROM vec_memories_2").fetchone()[0], 4)
            self.assertEqual(conn.execute("PRAGMA journal_mode").fetchone()[0], "wal")
            with self.assertRaises(sqlite3.OperationalError):
                conn.execute("SELECT load_extension('not-allowed')")

    def test_session_is_prefiltered_before_knn_not_oversampled_afterwards(self):
        self.embedding.vectors = {"own note": [0.1, 1.0]}
        store = self.make_store()
        for index in range(40):
            store.add_memory("other-session", f"closer distractor {index}")
        for index in range(3):
            self.embedding.vectors[f"own note {index}"] = [0.1, 1.0]
            store.add_memory("requested-session", f"own note {index}")

        results = store.search_memory("query", " requested-session ", limit=3)

        self.assertEqual(len(results), 3)
        self.assertEqual({item["session_id"] for item in results}, {"requested-session"})
        self.assertTrue(all(item["content"].startswith("own note") for item in results))
        self.assertEqual(store.search_memory("query", "unknown-session"), [])

    def test_sparse_sessions_share_storage_instead_of_oversharding(self):
        self.embedding.default = [1.0] + [0.0] * 767
        store = self.make_store()
        store.add_memory("session-0", "first note")
        initial_size = Path(self.path).stat().st_size
        for index in range(1, 12):
            store.add_memory(f"session-{index}", f"note {index}")
        self.assertLess(Path(self.path).stat().st_size, initial_size * 3)
        result = store.search_memory("query", "session-5", limit=3)
        self.assertEqual([item["content"] for item in result], ["note 5"])

    def test_session_ids_are_bound_parameters_and_trimmed(self):
        store = self.make_store()
        session = "a' OR 1=1 --"
        store.add_memory(f"  {session}  ", "selected")
        store.add_memory("other-session", "not selected")
        results = store.search_memory("query", session)
        self.assertEqual([item["content"] for item in results], ["selected"])
        self.assertEqual(results[0]["session_id"], session)

    def test_metadata_join_rechecks_the_session(self):
        store = self.make_store()
        store.add_memory("session-a", "private")
        with closing(store._get_connection()) as conn, conn:
            conn.execute("UPDATE memories SET session_id = 'session-b'")
        self.assertEqual(store.search_memory("query", "session-a"), [])
        self.assertEqual(store.search_memory("query", "session-b"), [])

    def test_each_dimension_has_a_separate_real_index(self):
        self.embedding.vectors = {"three": [1.0, 0.0, 0.0], "four": [1.0, 0.0, 0.0, 0.0]}
        store = self.make_store()
        store.add_memory("session-a", "two")
        store.add_memory("session-a", "three")
        self.assertEqual([r["content"] for r in store.search_memory("two", "session-a")], ["two"])
        self.assertEqual([r["content"] for r in store.search_memory("three", "session-a")], ["three"])
        self.assertEqual(store.search_memory("four", "session-a"), [])
        with closing(store._get_connection()) as conn:
            self.assertIsNone(conn.execute(
                "SELECT name FROM sqlite_master WHERE name = 'vec_memories_4'"
            ).fetchone())

    def test_data_survives_reopening_and_metadata_is_not_shared(self):
        store = self.make_store()
        metadata = {"source": {"name": "مصدر عربي"}}
        store.add_memory("session-a", "ذاكرة عربية", metadata)
        metadata["source"]["name"] = "changed"

        reopened = self.make_store()
        result = reopened.search_memory("query", "session-a")[0]
        self.assertEqual(result["content"], "ذاكرة عربية")
        self.assertEqual(result["metadata"], {"source": {"name": "مصدر عربي"}})
        result["metadata"]["source"]["name"] = "changed again"
        self.assertEqual(store.search_memory("query", "session-a")[0]["metadata"]["source"]["name"], "مصدر عربي")

    def test_private_memory_database_survives_operation_connections(self):
        store = self.make_store(":memory:")
        other = self.make_store(":memory:")
        self.addCleanup(store._anchor_finalizer)
        self.addCleanup(other._anchor_finalizer)
        store.add_memory("session-a", "ephemeral")
        self.assertEqual(store.search_memory("query", "session-a")[0]["content"], "ephemeral")
        self.assertEqual(other.search_memory("query", "session-a"), [])
        self.assertFalse(Path(":memory:").exists())

    def test_invalid_vectors_fail_without_any_partial_writes(self):
        store = self.make_store()
        invalid = (
            [], [True, 1], ["1", 2], [float("nan"), 1], [float("inf"), 1],
            [0.0, 0.0], [1e100, 1], [1e-100, 0], [1.0] * 8193, [10**400, 1], (1.0, 0.0),
        )
        for vector in invalid:
            with self.subTest(vector_type=type(vector).__name__, size=len(vector)):
                self.embedding.default = vector
                with self.assertRaises(MemoryStoreError):
                    store.add_memory("session-a", "content")
                with self.assertRaises(MemoryStoreError):
                    store.search_memory("query", "session-a")
        with closing(store._get_connection()) as conn:
            self.assertEqual(conn.execute("SELECT count(*) FROM memories").fetchone()[0], 0)
            self.assertIsNone(conn.execute(
                "SELECT name FROM sqlite_master WHERE name = 'vec_memories_2'"
            ).fetchone())

    def test_embedding_errors_are_raised_not_replaced_by_empty_results(self):
        store = self.make_store()
        for error in (MemoryStoreError("embedding unavailable"), RuntimeError("provider failed")):
            with self.subTest(error=type(error).__name__), patch.object(self.embedding, "embed", side_effect=error):
                with self.assertRaises(MemoryStoreError):
                    store.add_memory("session-a", "content")
                with self.assertRaises(MemoryStoreError):
                    store.search_memory("query", "session-a")

    def test_vector_insert_failure_rolls_back_metadata_and_new_index(self):
        store = self.make_store()
        ensure = store._ensure_vec_table

        def wrong_table(conn, dimension):
            ensure(conn, dimension)
            return "missing_vector_table"

        with patch.object(store, "_ensure_vec_table", side_effect=wrong_table):
            with self.assertRaises(MemoryStoreError):
                store.add_memory("session-a", "must not persist")
        with closing(store._get_connection()) as conn:
            self.assertEqual(conn.execute("SELECT count(*) FROM memories").fetchone()[0], 0)
            self.assertIsNone(conn.execute(
                "SELECT name FROM sqlite_master WHERE name = 'vec_memories_2'"
            ).fetchone())
        self.assertTrue(store.add_memory("session-a", "retry succeeds"))

    def test_connections_close_and_extension_loading_is_disabled(self):
        connections = []
        connect = sqlite3.connect

        def track(*args, **kwargs):
            conn = connect(*args, **kwargs, factory=TrackingConnection)
            connections.append(conn)
            return conn

        with patch("cela_sh.memory.sqlite_vec_store.sqlite3.connect", side_effect=track):
            store = self.make_store()
            store.add_memory("session-a", "content")
            store.search_memory("query", "session-a")
        self.assertEqual(len(connections), 3)
        self.assertTrue(all(conn.closed for conn in connections))
        self.assertTrue(all(conn.loading_flags == [True, False] for conn in connections))

    def test_unsupported_sqlite_version_fails_before_initialization(self):
        with patch("cela_sh.memory.sqlite_vec_store.sqlite3.sqlite_version_info", (3, 34, 0)):
            with self.assertRaisesRegex(MemoryStoreError, "3.35"):
                self.make_store()
        self.assertFalse(Path(self.path).exists())

    def test_extension_load_failure_has_no_python_fallback(self):
        with patch("cela_sh.memory.sqlite_vec_store.sqlite_vec.load", side_effect=sqlite3.OperationalError("no extension")):
            with self.assertRaisesRegex(MemoryStoreError, "load sqlite-vec"):
                self.make_store()

    def test_constructor_and_limits_are_validated(self):
        for timeout in (True, False, 0, -1, float("inf"), float("nan"), "5"):
            with self.subTest(timeout=timeout), self.assertRaises(ValueError):
                SqliteVecMemoryStore(self.path, self.embedding, timeout=timeout)
        for path in (None, "", "   "):
            with self.subTest(path=path), self.assertRaises(ValueError):
                SqliteVecMemoryStore(path, self.embedding)
        with self.assertRaises(MemoryStoreError):
            self.make_store(str(Path(self.path).parent / "missing" / "db.sqlite"))
        store = self.make_store()
        for limit in (True, False, 0, -1, 1.5, "3"):
            with self.subTest(limit=limit), self.assertRaises(ValueError):
                store.search_memory("query", "session-a", limit=limit)
        store.add_memory("session-a", "content")
        self.assertEqual(len(store.search_memory("query", "session-a", limit=100_000)), 1)

    def test_sqlite_search_errors_are_wrapped(self):
        store = self.make_store()
        with patch.object(store, "_get_connection", side_effect=sqlite3.OperationalError("database locked")):
            with self.assertRaisesRegex(MemoryStoreError, "search memories"):
                store.search_memory("query", "session-a")

    def test_concurrent_writes_keep_metadata_and_vectors_in_sync(self):
        store = self.make_store()
        with ThreadPoolExecutor(max_workers=4) as pool:
            written = list(pool.map(lambda index: store.add_memory("session-a", f"note {index}"), range(20)))
        self.assertTrue(all(written))
        self.assertEqual(len(store.search_memory("query", "session-a", limit=30)), 20)
        with closing(store._get_connection()) as conn:
            self.assertEqual(conn.execute("SELECT count(*) FROM memories").fetchone()[0], 20)
            self.assertEqual(conn.execute("SELECT count(*) FROM vec_memories_2").fetchone()[0], 20)


class SqliteVecMigrationTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path = str(Path(directory.name) / "legacy.db")
        self.embedding = VectorEmbedding({"three": [1.0, 0.0, 0.0]})

    def legacy_database(self, rows):
        with closing(sqlite3.connect(self.path)) as conn, conn:
            conn.execute("""CREATE TABLE memories (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL,
                content TEXT NOT NULL,
                metadata TEXT NOT NULL DEFAULT '{}',
                embedding BLOB NOT NULL
            )""")
            conn.execute("CREATE INDEX idx_memories_session_id ON memories (session_id)")
            conn.executemany("INSERT INTO memories VALUES (?, ?, ?, ?, ?)", rows)

    def test_legacy_blob_migration_is_lossless_idempotent_and_never_reembeds(self):
        metadata = json.dumps({"source": {"name": "ذاكرة قديمة"}}, ensure_ascii=False)
        self.legacy_database([
            (7, "session-a", "two", metadata, struct.pack("<2f", 10.0, 0.0)),
            (12, "session-b", "three", "{}", struct.pack("<3f", 1.0, 0.0, 0.0)),
        ])
        store = SqliteVecMemoryStore(self.path, self.embedding)
        self.assertEqual(self.embedding.inputs, [])
        self.assertEqual(store.search_memory("query", "session-a")[0]["metadata"], json.loads(metadata))
        self.assertEqual(store.search_memory("three", "session-b")[0]["content"], "three")
        self.assertTrue(store.add_memory("session-a", "new"))
        SqliteVecMemoryStore(self.path, self.embedding)
        with closing(store._get_connection()) as conn:
            rows = conn.execute("SELECT id, content, embedding_dim FROM memories ORDER BY id").fetchall()
            self.assertEqual([tuple(row) for row in rows], [(7, "two", 2), (12, "three", 3), (13, "new", 2)])
            self.assertEqual(conn.execute("SELECT count(*) FROM vec_memories_2").fetchone()[0], 2)
            self.assertEqual(conn.execute("SELECT count(*) FROM vec_memories_3").fetchone()[0], 1)
            self.assertIsNone(conn.execute(
                "SELECT name FROM sqlite_master WHERE name = 'cela_memories_vec_migration'"
            ).fetchone())

    def test_corrupt_legacy_blob_rolls_back_the_entire_migration(self):
        self.legacy_database([
            (1, "session-a", "good", "{}", struct.pack("<2f", 1.0, 0.0)),
            (2, "session-b", "corrupt", "{}", b"bad"),
        ])
        with self.assertRaises(MemoryStoreError):
            SqliteVecMemoryStore(self.path, self.embedding)
        with closing(sqlite3.connect(self.path)) as conn:
            columns = {row[1] for row in conn.execute("PRAGMA table_info(memories)")}
            self.assertIn("embedding", columns)
            self.assertNotIn("embedding_dim", columns)
            self.assertEqual(conn.execute("SELECT count(*) FROM memories").fetchone()[0], 2)
            tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master")}
            self.assertFalse(any(name.startswith("vec_memories_") for name in tables))
            self.assertNotIn("cela_memories_vec_migration", tables)
            self.assertIn("idx_memories_session_id", tables)
        self.assertEqual(self.embedding.inputs, [])

    def test_zero_legacy_vector_fails_closed_without_deleting_data(self):
        self.legacy_database([(1, "session-a", "zero", "{}", struct.pack("<2f", 0.0, 0.0))])
        with self.assertRaisesRegex(MemoryStoreError, "finite"):
            SqliteVecMemoryStore(self.path, self.embedding)
        with closing(sqlite3.connect(self.path)) as conn:
            self.assertEqual(conn.execute("SELECT content FROM memories").fetchone()[0], "zero")
            self.assertIn("embedding", {row[1] for row in conn.execute("PRAGMA table_info(memories)")})

    def test_empty_legacy_database_is_migrated(self):
        self.legacy_database([])
        store = SqliteVecMemoryStore(self.path, self.embedding)
        self.assertEqual(store.search_memory("query", "session-a"), [])
        with closing(store._get_connection()) as conn:
            self.assertNotIn("embedding", {row["name"] for row in conn.execute("PRAGMA table_info(memories)")})

    def test_unsupported_schema_is_not_silently_replaced(self):
        with closing(sqlite3.connect(self.path)) as conn, conn:
            conn.execute("CREATE TABLE memories (id INTEGER, unexpected TEXT)")
        with self.assertRaisesRegex(MemoryStoreError, "unsupported schema"):
            SqliteVecMemoryStore(self.path, self.embedding)
        with closing(sqlite3.connect(self.path)) as conn:
            self.assertIn("unexpected", {row[1] for row in conn.execute("PRAGMA table_info(memories)")})


if __name__ == "__main__":
    unittest.main()
