"""Fault-isolation and concurrency tests for the planning API.

Proves the core guarantees:

* a dead or slow optional dependency (planner / memory store) degrades only
  its own functions with a typed 503 — liveness and the other dependency keep
  working, and no substitute plan or result is ever invented;
* circuit breakers fail fast after repeated failures, report their state on
  ``/readyz``, and recover automatically once the dependency returns;
* the old process-wide request lock is gone: concurrent requests run in
  parallel, and the thread pool cap is honored as backpressure;
* SQLite stays consistent under concurrent writers, and a write that fails
  mid-transaction is reported as a failure (503) and persists nothing.
"""

import json
import sqlite3
import tempfile
import threading
import time
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import Mock, patch

import requests
from fastapi.testclient import TestClient

from cela_sh import OllamaPlanner, Settings, SqliteVecMemoryStore
from cela_sh.memory import MemoryStoreError
from cela_sh.server import create_app

from fakes import DeterministicEmbedding

RAW_PLAN = {
    "analysis": "Read the public page.",
    "steps": [{"action": "OBSERVE", "target": "page", "value": "", "reason": "Read it"}],
}


def make_ok_response():
    response = Mock(status_code=200)
    response.raise_for_status.return_value = None
    response.json.return_value = {"response": json.dumps(RAW_PLAN)}
    return response


class FaultIsolationTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path = str(Path(directory.name) / "api.db")
        self.store = SqliteVecMemoryStore(self.path, DeterministicEmbedding())
        self.planner = OllamaPlanner(memory_store=self.store)
        self.addCleanup(self.planner._session.close)
        self.settings = Settings(memory_backend="sqlite-vec", sqlite_vec_path=self.path)
        self.app = create_app(self.settings, self.planner)
        self.client = self.enterContext(TestClient(self.app))
        self.post = self.enterContext(patch.object(self.planner._session, "post", return_value=make_ok_response()))
        self.planner_breaker = self.app.state.planner_breaker
        self.memory_breaker = self.app.state.memory_breaker

    # -- liveness vs dependency state -------------------------------------

    def test_planner_outage_leaves_memory_and_liveness_working(self):
        self.assertEqual(self.client.post("/plan", json={"goal": "Read", "session_id": "s"}).status_code, 200)

        with patch.object(self.planner._session, "post", side_effect=requests.ConnectionError("refused")):
            plan = self.client.post("/plan", json={"goal": "Read", "session_id": "s"})
            self.assertEqual(plan.status_code, 503)
            self.assertEqual(plan.json()["error"]["code"], "planner_connection_error")
            self.assertNotIn("steps", plan.json())

            memory = self.client.post("/memory", json={"session_id": "s", "content": "note"})
            self.assertEqual(memory.status_code, 201)
            search = self.client.post("/memory/search", json={"session_id": "s", "query": "note"})
            self.assertEqual(search.status_code, 200)

        health = self.client.get("/health")
        self.assertEqual(health.status_code, 200)
        self.assertEqual(health.json()["status"], "ok")

        ready = self.client.get("/readyz")
        self.assertEqual(ready.status_code, 200)
        body = ready.json()
        self.assertEqual(body["status"], "ok")
        self.assertTrue(body["live"])
        self.assertEqual(body["dependencies"]["planner"]["state"], "unavailable")
        self.assertEqual(body["dependencies"]["memory"]["state"], "healthy")

    def test_memory_outage_leaves_planner_and_liveness_working(self):
        self.assertEqual(self.client.post("/plan", json={"goal": "Read", "session_id": "s"}).status_code, 200)

        detail = "qdrant: connection refused"
        calls_before = self.post.call_count
        with (
            patch.object(self.store, "add_memory", side_effect=MemoryStoreError(detail)),
            patch.object(self.store, "search_memory", side_effect=MemoryStoreError(detail)),
            patch.object(self.store, "list_memory", side_effect=MemoryStoreError(detail)),
        ):
            memory = self.client.post("/memory", json={"session_id": "s", "content": "note"})
            self.assertEqual(memory.status_code, 503)
            self.assertEqual(memory.json()["error"]["code"], "memory_store_error")
            self.assertNotIn(detail, memory.text)

            search = self.client.post("/memory/search", json={"session_id": "s", "query": "note"})
            self.assertEqual(search.status_code, 503)

            plan = self.client.post("/plan", json={"goal": "Read", "session_id": "s"})
            self.assertEqual(plan.status_code, 503)
            self.assertEqual(plan.json()["error"]["code"], "planner_memory_error")

        # The failed in-plan memory lookup must not have reached Ollama, and
        # it must not have been attributed to the planner circuit.
        self.assertEqual(self.post.call_count, calls_before)
        self.assertEqual(self.planner_breaker.state, "closed")

        self.assertEqual(self.client.get("/health").status_code, 200)
        ready = self.client.get("/readyz")
        self.assertEqual(ready.status_code, 200)
        self.assertEqual(ready.json()["dependencies"]["memory"]["state"], "unavailable")
        self.assertEqual(ready.json()["dependencies"]["planner"]["state"], "healthy")

    def test_readyz_reports_honest_defaults_before_any_traffic(self):
        ready = self.client.get("/readyz")
        self.assertEqual(ready.status_code, 200)
        body = ready.json()
        self.assertEqual(body["dependencies"]["planner"]["state"], "unknown")
        self.assertEqual(body["dependencies"]["memory"]["state"], "unknown")
        self.assertEqual(body["dependencies"]["planner"]["circuit"], "closed")
        self.assertEqual(body["dry_run"], True)
        self.assertEqual(body["execution_enabled"], False)

    def test_readyz_reports_disabled_memory(self):
        bare_planner = OllamaPlanner()
        self.addCleanup(bare_planner._session.close)
        application = create_app(Settings(), bare_planner)
        with TestClient(application) as client:
            ready = client.get("/readyz")
        self.assertEqual(ready.status_code, 200)
        self.assertEqual(ready.json()["dependencies"]["memory"], {"state": "disabled"})

    # -- circuit breakers ---------------------------------------------------

    def _fail_planner_times(self, times: int):
        for _ in range(times):
            with patch.object(self.planner._session, "post", side_effect=requests.ConnectionError("refused")):
                response = self.client.post("/plan", json={"goal": "Read", "session_id": "s"})
            self.assertEqual(response.status_code, 503)
            self.assertEqual(response.json()["error"]["code"], "planner_connection_error")

    def test_planner_circuit_opens_fails_fast_and_recovers(self):
        self._fail_planner_times(3)
        self.assertEqual(self.planner_breaker.state, "open")

        # While open: fast 503 with a stable code, and no HTTP call at all.
        with patch.object(self.planner._session, "post", return_value=make_ok_response()) as ok_post:
            response = self.client.post("/plan", json={"goal": "Read", "session_id": "s"})
            self.assertEqual(response.status_code, 503)
            self.assertEqual(response.json()["error"]["code"], "planner_circuit_open")
            self.assertNotIn("steps", response.json())
            ok_post.assert_not_called()

        ready = self.client.get("/readyz")
        self.assertEqual(ready.json()["dependencies"]["planner"]["circuit"], "open")

        # Cooldown elapses: the next request is the single probe, and a
        # successful probe closes the circuit (recovery without an operator).
        self.planner_breaker._opened_at = self.planner_breaker._clock() - self.planner_breaker.cooldown_seconds - 0.01
        with patch.object(self.planner._session, "post", return_value=make_ok_response()) as ok_post:
            response = self.client.post("/plan", json={"goal": "Read", "session_id": "s"})
            self.assertEqual(response.status_code, 200)
            self.assertTrue(response.json()["dry_run"])
            ok_post.assert_called_once()

        self.assertEqual(self.planner_breaker.state, "closed")
        ready = self.client.get("/readyz")
        deps = ready.json()["dependencies"]["planner"]
        self.assertEqual(deps["state"], "healthy")
        self.assertEqual(deps["circuit"], "closed")

    def test_failed_probe_reopens_the_planner_circuit(self):
        self._fail_planner_times(3)
        self.planner_breaker._opened_at = self.planner_breaker._clock() - self.planner_breaker.cooldown_seconds - 0.01
        with patch.object(self.planner._session, "post", side_effect=requests.ConnectionError("still down")) as fail_post:
            response = self.client.post("/plan", json={"goal": "Read", "session_id": "s"})
            self.assertEqual(response.status_code, 503)
            self.assertEqual(response.json()["error"]["code"], "planner_connection_error")
            fail_post.assert_called_once()
            self.assertEqual(self.planner_breaker.state, "open")

            # Still open: the next request fails fast without touching Ollama.
            response = self.client.post("/plan", json={"goal": "Read", "session_id": "s"})
            self.assertEqual(response.status_code, 503)
            self.assertEqual(response.json()["error"]["code"], "planner_circuit_open")
            self.assertEqual(fail_post.call_count, 1)

    def test_memory_circuit_isolates_from_the_planner(self):
        detail = "store down"
        with patch.object(self.store, "add_memory", side_effect=MemoryStoreError(detail)):
            for _ in range(3):
                response = self.client.post("/memory", json={"session_id": "s", "content": "note"})
                self.assertEqual(response.status_code, 503)
        self.assertEqual(self.memory_breaker.state, "open")

        # The memory routes fail fast without touching the store...
        with patch.object(self.store, "add_memory", side_effect=MemoryStoreError(detail)) as store_post:
            response = self.client.post("/memory", json={"session_id": "s", "content": "note"})
            self.assertEqual(response.status_code, 503)
            self.assertEqual(response.json()["error"]["code"], "memory_circuit_open")
            store_post.assert_not_called()

        # ...and the planner circuit stays closed. With memory enabled, /plan
        # depends on the store, so it fails fast with the honest typed error
        # instead of inventing a plan or waiting on the dead store.
        self.assertEqual(self.planner_breaker.state, "closed")
        calls_before = self.post.call_count
        response = self.client.post("/plan", json={"goal": "Read", "session_id": "s"})
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()["error"]["code"], "planner_memory_error")
        self.assertEqual(self.post.call_count, calls_before)
        self.assertNotIn("steps", response.json())

    def test_memory_circuit_recovers_when_the_store_returns(self):
        with patch.object(self.store, "add_memory", side_effect=MemoryStoreError("down")):
            for _ in range(3):
                self.assertEqual(self.client.post("/memory", json={"session_id": "s", "content": "n"}).status_code, 503)
        self.assertEqual(self.memory_breaker.state, "open")

        # Store is healthy again; after the cooldown the next request probes.
        self.memory_breaker._opened_at = self.memory_breaker._clock() - self.memory_breaker.cooldown_seconds - 0.01
        self.assertEqual(self.client.post("/memory", json={"session_id": "s", "content": "note"}).status_code, 201)
        self.assertEqual(self.memory_breaker.state, "closed")
        self.assertEqual(
            self.client.post("/plan", json={"goal": "Read", "session_id": "s"}).status_code,
            200,
        )

    def test_input_validation_does_not_trip_the_breaker(self):
        for _ in range(5):
            response = self.client.post("/plan", json={"goal": " "})
            self.assertEqual(response.status_code, 422)
        self.assertEqual(self.planner_breaker.state, "closed")
        self.assertEqual(self.planner_breaker.consecutive_failures, 0)
        self.assertEqual(self.client.get("/readyz").json()["dependencies"]["planner"]["state"], "unknown")

    # -- concurrency: no process-wide lock ---------------------------------

    def test_concurrent_plans_run_in_parallel(self):
        count = 8
        per_call = 0.25
        active = 0
        max_active = 0
        guard = threading.Lock()
        errors: list[str] = []

        def slow_post(*args, **kwargs):
            nonlocal active, max_active
            with guard:
                active += 1
                max_active = max(max_active, active)
            try:
                time.sleep(per_call)
                return make_ok_response()
            finally:
                with guard:
                    active -= 1

        def hit(index: int) -> None:
            try:
                response = self.client.post(
                    "/plan", json={"goal": f"Read page {index}", "session_id": f"s-{index}"}
                )
                if response.status_code != 200:
                    errors.append(f"plan {index}: {response.status_code} {response.text}")
            except Exception as exc:  # pragma: no cover - failure report only
                errors.append(f"plan {index}: {exc!r}")

        with patch.object(self.planner._session, "post", side_effect=slow_post):
            started = time.monotonic()
            threads = [threading.Thread(target=hit, args=(i,)) for i in range(count)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()
            elapsed = time.monotonic() - started

        self.assertEqual(errors, [])
        self.assertGreaterEqual(max_active, 2, "requests must overlap, not serialize behind one lock")
        self.assertLess(elapsed, count * per_call / 2, "serialized execution would take ~2s")

    def test_mixed_concurrent_routes_do_not_block_each_other(self):
        def slow_post(*args, **kwargs):
            time.sleep(0.2)
            return make_ok_response()

        errors: list[str] = []

        def run(path: str, payload: dict, index: int) -> None:
            try:
                response = self.client.post(path, json=payload)
                if response.status_code not in (200, 201):
                    errors.append(f"{path} {index}: {response.status_code} {response.text}")
            except Exception as exc:  # pragma: no cover - failure report only
                errors.append(f"{path} {index}: {exc!r}")

        jobs = []
        with patch.object(self.planner._session, "post", side_effect=slow_post):
            for i in range(4):
                jobs.append(threading.Thread(target=run, args=("/plan", {"goal": f"Read {i}", "session_id": f"s-{i}"}, i)))
                jobs.append(threading.Thread(target=run, args=("/memory", {"session_id": f"s-{i}", "content": f"note {i}"}, i)))
                jobs.append(threading.Thread(target=run, args=("/memory/search", {"session_id": f"s-{i}", "query": f"note {i}"}, i)))
            for thread in jobs:
                thread.start()
            for thread in jobs:
                thread.join()
        self.assertEqual(errors, [])

    def test_concurrent_sqlite_writes_are_all_durable(self):
        per_worker = 5
        errors: list[BaseException] = []

        def writer(worker: int) -> None:
            try:
                for index in range(per_worker):
                    self.assertTrue(self.store.add_memory(f"session-{worker}", f"note {worker} {index}"))
            except BaseException as exc:  # pragma: no cover - failure report only
                errors.append(exc)

        def searcher() -> None:
            try:
                for _ in range(20):
                    results = self.store.search_memory("note", "session-0", limit=5)
                    for memory in results:
                        self.assertEqual(memory["session_id"], "session-0")
            except BaseException as exc:  # pragma: no cover - failure report only
                errors.append(exc)

        threads = [threading.Thread(target=writer, args=(w,)) for w in range(8)]
        threads.append(threading.Thread(target=searcher))
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        self.assertEqual(errors, [])
        with closing(sqlite3.connect(self.path)) as conn:
            count = conn.execute("SELECT count(*) FROM memories").fetchone()[0]
        self.assertEqual(count, 8 * per_worker)

    # -- failed writes are never hidden as success --------------------------

    def test_api_write_failure_is_a_503_and_stores_nothing(self):
        with patch.object(self.store, "add_memory", return_value=False):
            response = self.client.post("/memory", json={"session_id": "s", "content": "note"})
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()["error"]["code"], "memory_store_error")
        with closing(sqlite3.connect(self.path)) as conn:
            self.assertEqual(conn.execute("SELECT count(*) FROM memories").fetchone()[0], 0)

    def test_mid_transaction_failure_rolls_back_and_the_store_recovers(self):
        self.assertTrue(self.store.add_memory("s", "first"))
        real_get_connection = self.store._get_connection

        def failing_get():
            connection = real_get_connection()
            return _FailingWriteProxy(connection, "vec_memories")

        with patch.object(self.store, "_get_connection", side_effect=failing_get):
            with self.assertRaises(MemoryStoreError):
                self.store.add_memory("s", "second")

        # The partial write (memories row + vec row) must be fully rolled back.
        with closing(sqlite3.connect(self.path)) as conn:
            count = conn.execute("SELECT count(*) FROM memories").fetchone()[0]
            sessions = {row[0] for row in conn.execute("SELECT DISTINCT session_id FROM memories")}
            contents = [row[0] for row in conn.execute("SELECT content FROM memories ORDER BY id")]
        self.assertEqual(count, 1)
        self.assertEqual(sessions, {"s"})
        self.assertEqual(contents, ["first"])
        # The vector side of the same transaction rolled back too. vec0 keeps
        # one row per stored vector in its <table>_rowids shadow table.
        with closing(sqlite3.connect(self.path)) as conn:
            vec_tables = [
                row[0]
                for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE name LIKE 'vec_memories_%_rowids' AND type = 'table'"
                )
            ]
            self.assertEqual(len(vec_tables), 1)
            vec_count = conn.execute(f"SELECT count(*) FROM {vec_tables[0]}").fetchone()[0]
        self.assertEqual(vec_count, 1)

        # And the same store handles the next write fine: recovery is real.
        self.assertTrue(self.store.add_memory("s", "third"))
        with closing(sqlite3.connect(self.path)) as conn:
            self.assertEqual(conn.execute("SELECT count(*) FROM memories").fetchone()[0], 2)

    # -- liveness and backpressure ------------------------------------------

    def test_health_answers_while_the_threadpool_is_saturated(self):
        settings = Settings(
            memory_backend="sqlite-vec", sqlite_vec_path=self.path, max_concurrent_requests=1
        )
        application = create_app(settings, self.planner)
        entered = threading.Event()

        def hold(*args, **kwargs):
            entered.set()
            time.sleep(0.5)
            return make_ok_response()

        with TestClient(application) as client, patch.object(self.planner._session, "post", side_effect=hold):
            worker = threading.Thread(
                target=lambda: client.post("/plan", json={"goal": "Read", "session_id": "s"})
            )
            worker.start()
            self.assertTrue(entered.wait(2.0), "the slow plan must occupy the only pool token")
            started = time.monotonic()
            response = client.get("/health")
            elapsed = time.monotonic() - started
            self.assertEqual(response.status_code, 200)
            self.assertLess(elapsed, 0.3, "liveness must not wait on a saturated thread pool")
            worker.join()

    def test_threadpool_cap_limits_concurrency(self):
        settings = Settings(
            memory_backend="sqlite-vec", sqlite_vec_path=self.path, max_concurrent_requests=2
        )
        application = create_app(settings, self.planner)
        active = 0
        max_active = 0
        guard = threading.Lock()
        errors: list[str] = []

        def slow_post(*args, **kwargs):
            nonlocal active, max_active
            with guard:
                active += 1
                max_active = max(max_active, active)
            try:
                time.sleep(0.3)
                return make_ok_response()
            finally:
                with guard:
                    active -= 1

        def hit(client, index: int) -> None:
            try:
                response = client.post("/plan", json={"goal": f"Read {index}", "session_id": f"s-{index}"})
                if response.status_code != 200:
                    errors.append(f"{index}: {response.status_code}")
            except Exception as exc:  # pragma: no cover - failure report only
                errors.append(f"{index}: {exc!r}")

        # One shared client: every request goes through the same portal, so
        # the cap set by this app's lifespan is the one under test.
        with TestClient(application) as client, patch.object(
            self.planner._session, "post", side_effect=slow_post
        ):
            threads = [threading.Thread(target=hit, args=(client, i)) for i in range(4)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()

        self.assertEqual(errors, [])
        self.assertGreaterEqual(max_active, 1)
        self.assertLessEqual(max_active, 2, "the bulkhead must cap in-flight synchronous handlers")


class _FailingWriteProxy:
    """Wraps a real SQLite connection; fails on one statement fragment.

    Simulates a mid-transaction write failure (e.g. disk full) so the test can
    prove the transaction rolls back instead of persisting a half-written row.
    """

    def __init__(self, connection: sqlite3.Connection, fail_fragment: str) -> None:
        self._connection = connection
        self._fail_fragment = fail_fragment
        self.failed = False

    def execute(self, sql: str, *args, **kwargs):
        if not self.failed and self._fail_fragment in sql:
            self.failed = True
            raise sqlite3.OperationalError("simulated mid-transaction write failure")
        return self._connection.execute(sql, *args, **kwargs)

    def __enter__(self):
        return self._connection.__enter__()

    def __exit__(self, exc_type, exc_value, traceback):
        return self._connection.__exit__(exc_type, exc_value, traceback)

    def __getattr__(self, item):
        return getattr(self._connection, item)


if __name__ == "__main__":
    unittest.main()
