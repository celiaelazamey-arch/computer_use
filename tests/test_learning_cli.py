import io
import json
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import Mock, call, patch

from cela_sh.cli import main
from cela_sh.memory import MemoryStoreError
from cela_sh.memory.scopes import GLOBAL_MEMORY_SESSION_ID
from cela_sh.settings import ConfigurationError, Settings


class LearningCliTests(unittest.TestCase):
    def make_store(self):
        store = Mock()
        store.add_memory.return_value = True
        store.replace_source.side_effect = lambda session, source_path, source_hash, records: {
            "status": "imported",
            "chunks_stored": len(records),
            "replaced_chunks": 0,
            "existing_chunks": len(records),
        }
        planner = Mock(memory_store=store)
        settings = Settings(memory_backend="sqlite-vec")
        return settings, store, planner

    def test_learn_stores_explicit_type_tags_and_project_scope(self):
        settings, store, planner = self.make_store()
        stdout = io.StringIO()
        stderr = io.StringIO()

        with patch("cela_sh.learning_cli.Settings.from_env", return_value=settings), patch(
            "cela_sh.learning_cli.create_planner", return_value=planner
        ) as factory, redirect_stdout(stdout), redirect_stderr(stderr):
            exit_code = main(
                [
                    "learn",
                    "--session-id",
                    "project-notes",
                    "--type",
                    "correction",
                    "--tag",
                    "testing",
                    "--tag",
                    "Deployment",
                    "--content",
                    "Never use production during tests.",
                ]
            )

        self.assertEqual(exit_code, 0)
        factory.assert_called_once_with(settings)
        store.add_memory.assert_called_once_with(
            "project-notes",
            "Never use production during tests.",
            {
                "scope": "project",
                "type": "correction",
                "tags": ["deployment", "testing"],
                "source": "explicit_cli_learning",
            },
        )
        planner.generate_plan.assert_not_called()
        self.assertEqual(
            json.loads(stdout.getvalue()),
            {
                "learned": True,
                "scope": "project",
                "type": "correction",
                "tags": ["deployment", "testing"],
                "memory_backend": "sqlite-vec",
                "session_id": "project-notes",
            },
        )
        self.assertEqual(stderr.getvalue(), "")

    def test_global_learning_uses_reserved_store_key_without_session_id(self):
        settings, store, planner = self.make_store()
        stdout = io.StringIO()
        with patch("cela_sh.learning_cli.Settings.from_env", return_value=settings), patch(
            "cela_sh.learning_cli.create_planner", return_value=planner
        ), redirect_stdout(stdout):
            exit_code = main(["learn", "--scope", "global", "--content", "Shared rule."])

        self.assertEqual(exit_code, 0)
        store.add_memory.assert_called_once_with(
            GLOBAL_MEMORY_SESSION_ID,
            "Shared rule.",
            {
                "scope": "global",
                "type": "note",
                "tags": [],
                "source": "explicit_cli_learning",
            },
        )
        self.assertNotIn("session_id", json.loads(stdout.getvalue()))

    def test_learning_requires_an_enabled_memory_backend(self):
        planner = Mock(memory_store=None)
        stdout = io.StringIO()
        stderr = io.StringIO()

        with patch("cela_sh.learning_cli.Settings.from_env", return_value=Settings()), patch(
            "cela_sh.learning_cli.create_planner", return_value=planner
        ) as factory, redirect_stdout(stdout), redirect_stderr(stderr):
            exit_code = main(["learn", "--session-id", "notes", "--content", "Remember this fact."])

        self.assertEqual(exit_code, 2)
        self.assertEqual(stdout.getvalue(), "")
        self.assertEqual(json.loads(stderr.getvalue())["error"]["code"], "configuration_error")
        self.assertIn("CELA_MEMORY_BACKEND", json.loads(stderr.getvalue())["error"]["message"])
        factory.assert_not_called()

    def test_store_failure_uses_the_existing_memory_error_contract(self):
        settings, store, planner = self.make_store()
        store.add_memory.side_effect = MemoryStoreError("embedding service unavailable")
        stdout = io.StringIO()
        stderr = io.StringIO()

        with patch("cela_sh.learning_cli.Settings.from_env", return_value=settings), patch(
            "cela_sh.learning_cli.create_planner", return_value=planner
        ), redirect_stdout(stdout), redirect_stderr(stderr):
            exit_code = main(["learn", "--session-id", "notes", "--content", "Remember this fact."])

        self.assertEqual(exit_code, 6)
        self.assertEqual(stdout.getvalue(), "")
        self.assertEqual(
            json.loads(stderr.getvalue()),
            {"error": {"code": "planner_memory_error", "message": "embedding service unavailable"}},
        )

    def test_configuration_errors_are_json(self):
        stdout = io.StringIO()
        stderr = io.StringIO()
        with patch(
            "cela_sh.learning_cli.Settings.from_env", side_effect=ConfigurationError("bad settings")
        ), redirect_stdout(stdout), redirect_stderr(stderr):
            exit_code = main(["learn", "--session-id", "notes", "--content", "Remember this fact."])

        self.assertEqual(exit_code, 2)
        self.assertEqual(stdout.getvalue(), "")
        self.assertEqual(json.loads(stderr.getvalue())["error"]["message"], "bad settings")

    def test_scope_validation_is_explicit_for_learn_and_search(self):
        for arguments in (
            ["learn", "--content", "Remember this fact."],
            ["learn", "--scope", "global", "--session-id", "project", "--content", "Remember this fact."],
            ["search", "--tag", "testing"],
            ["search", "--scope", "global", "--session-id", "project"],
        ):
            with self.subTest(arguments=arguments):
                stderr = io.StringIO()
                with redirect_stderr(stderr):
                    with self.assertRaises(SystemExit) as raised:
                        main(arguments)
                self.assertEqual(raised.exception.code, 2)
                self.assertEqual(json.loads(stderr.getvalue())["error"]["code"], "cli_usage_error")

    def test_content_must_be_nonblank_and_within_the_store_limit(self):
        for content in ("   ", "x" * 8_001):
            with self.subTest(length=len(content)):
                stderr = io.StringIO()
                with redirect_stderr(stderr):
                    with self.assertRaises(SystemExit) as raised:
                        main(["learn", "--session-id", "notes", "--content", content])
                self.assertEqual(raised.exception.code, 2)
                self.assertEqual(json.loads(stderr.getvalue())["error"]["code"], "cli_usage_error")

    def test_content_can_be_read_from_stdin(self):
        settings, store, planner = self.make_store()
        stdout = io.StringIO()

        with patch("cela_sh.learning_cli.Settings.from_env", return_value=settings), patch(
            "cela_sh.learning_cli.create_planner", return_value=planner
        ), patch("cela_sh.learning_cli.sys.stdin", io.StringIO("A multi-line\nnote.\n")), redirect_stdout(stdout):
            exit_code = main(["learn", "--session-id", "notes", "--content", "-"])

        self.assertEqual(exit_code, 0)
        store.add_memory.assert_called_once_with(
            "notes",
            "A multi-line\nnote.\n",
            {"scope": "project", "type": "note", "tags": [], "source": "explicit_cli_learning"},
        )

    def test_oversized_stdin_is_rejected_before_initializing_memory(self):
        stderr = io.StringIO()
        with patch("cela_sh.learning_cli.sys.stdin", io.StringIO("x" * 8_001)), patch(
            "cela_sh.learning_cli.create_planner"
        ) as factory, redirect_stderr(stderr):
            exit_code = main(["learn", "--session-id", "notes", "--content", "-"])

        self.assertEqual(exit_code, 2)
        self.assertEqual(json.loads(stderr.getvalue())["error"]["code"], "cli_usage_error")
        factory.assert_not_called()

    def test_learn_file_replaces_by_source_and_records_per_chunk_hashes(self):
        settings, store, planner = self.make_store()
        stdout = io.StringIO()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "README.md"
            path.write_text("\n\n".join("architecture note " + ("word " * 180) for _ in range(30)), encoding="utf-8")
            with patch("cela_sh.learning_cli.Settings.from_env", return_value=settings), patch(
                "cela_sh.learning_cli.create_planner", return_value=planner
            ), redirect_stdout(stdout):
                exit_code = main(
                    [
                        "learn-file",
                        str(path),
                        "--session-id",
                        "project-notes",
                        "--type",
                        "note",
                        "--tag",
                        "architecture",
                    ]
                )

        self.assertEqual(exit_code, 0)
        store.add_memory.assert_not_called()
        store.replace_source.assert_called_once()
        session, source_path, source_hash, records = store.replace_source.call_args.args
        self.assertEqual(session, "project-notes")
        self.assertTrue(source_path.startswith("external/"))
        self.assertFalse(Path(source_path).is_absolute())
        self.assertEqual(len(source_hash), 64)
        self.assertGreater(len(records), 1)
        for index, (chunk, metadata) in enumerate(records, start=1):
            self.assertLessEqual(len(chunk), 6_000)
            self.assertEqual(metadata["source_name"], "README.md")
            self.assertEqual(metadata["source_path"], source_path)
            self.assertEqual(metadata["source_hash"], source_hash)
            self.assertEqual(len(metadata["content_hash"]), 64)
            self.assertEqual(metadata["chunk_index"], index)
            self.assertEqual(metadata["chunk_count"], len(records))
            self.assertTrue(metadata["imported_at"])
            self.assertEqual(metadata["source"], "explicit_cli_file_learning")
            self.assertEqual(metadata["tags"], ["architecture"])
        output = json.loads(stdout.getvalue())
        self.assertEqual(output["chunks_stored"], len(records))
        self.assertEqual(output["source_path"], source_path)
        self.assertEqual(output["source_hash"], source_hash)
        self.assertEqual(output["status"], "imported")
        self.assertEqual(output["scope"], "project")

    def test_reimport_of_unchanged_source_reports_no_changes(self):
        settings, store, planner = self.make_store()
        store.replace_source.side_effect = None
        store.replace_source.return_value = {
            "status": "unchanged",
            "chunks_stored": 0,
            "replaced_chunks": 0,
            "existing_chunks": 1,
        }
        stdout = io.StringIO()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "guide.md"
            path.write_text("same source content", encoding="utf-8")
            with patch("cela_sh.learning_cli.Settings.from_env", return_value=settings), patch(
                "cela_sh.learning_cli.create_planner", return_value=planner
            ), redirect_stdout(stdout):
                exit_code = main(["learn-file", str(path), "--session-id", "project-a"])

        self.assertEqual(exit_code, 0)
        result = json.loads(stdout.getvalue())
        self.assertEqual(result["status"], "unchanged")
        self.assertFalse(result["learned"])
        self.assertFalse(result["metadata_applied"])
        self.assertIn("No changes detected", result["message"])
        self.assertIn("tags/type were retained", result["message"])
        self.assertEqual(result["chunks_stored"], 0)
        self.assertEqual(result["existing_chunks"], 1)

    def test_updated_source_reports_replacement_counts(self):
        settings, store, planner = self.make_store()
        store.replace_source.side_effect = None
        store.replace_source.return_value = {
            "status": "updated",
            "chunks_stored": 47,
            "replaced_chunks": 42,
            "existing_chunks": 47,
        }
        stdout = io.StringIO()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "guide.md"
            path.write_text("updated source", encoding="utf-8")
            with patch("cela_sh.learning_cli.Settings.from_env", return_value=settings), patch(
                "cela_sh.learning_cli.create_planner", return_value=planner
            ), redirect_stdout(stdout):
                exit_code = main(["learn-file", str(path), "--session-id", "project-a"])

        self.assertEqual(exit_code, 0)
        result = json.loads(stdout.getvalue())
        self.assertEqual(result["status"], "updated")
        self.assertEqual(result["replaced_chunks"], 42)
        self.assertEqual(result["chunks_stored"], 47)
        self.assertIn("replaced 42 chunks with 47 chunks", result["message"])

    def test_learn_file_reports_a_failed_atomic_source_replacement(self):
        settings, store, planner = self.make_store()
        store.replace_source.side_effect = MemoryStoreError("embedding offline")
        stdout = io.StringIO()
        stderr = io.StringIO()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "notes.txt"
            path.write_text("fact " * 3_000, encoding="utf-8")
            with patch("cela_sh.learning_cli.Settings.from_env", return_value=settings), patch(
                "cela_sh.learning_cli.create_planner", return_value=planner
            ), redirect_stdout(stdout), redirect_stderr(stderr):
                exit_code = main(["learn-file", str(path), "--session-id", "notes"])

        self.assertEqual(exit_code, 6)
        self.assertEqual(stdout.getvalue(), "")
        self.assertEqual(json.loads(stderr.getvalue())["error"]["code"], "planner_memory_error")

    def test_search_by_tag_lists_matching_records_without_semantic_query(self):
        settings, store, planner = self.make_store()
        store.list_memory.return_value = [
            {
                "session_id": "project-notes",
                "content": "Never use production in tests.",
                "metadata": {"scope": "project", "type": "correction", "tags": ["testing"]},
                "score": 0.0,
            }
        ]
        stdout = io.StringIO()
        with patch("cela_sh.learning_cli.Settings.from_env", return_value=settings), patch(
            "cela_sh.learning_cli.create_planner", return_value=planner
        ), redirect_stdout(stdout):
            exit_code = main(["search", "--session-id", "project-notes", "--tag", "Testing"])

        self.assertEqual(exit_code, 0)
        store.list_memory.assert_called_once_with("project-notes", limit=20, tags=["testing"], memory_type=None)
        result = json.loads(stdout.getvalue())
        self.assertEqual(result["count"], 1)
        self.assertEqual(result["results"][0]["type"], "correction")
        self.assertEqual(result["results"][0]["scope"], "project")

    def test_search_both_scopes_uses_separate_session_filters(self):
        settings, store, planner = self.make_store()
        store.list_memory.return_value = []
        with patch("cela_sh.learning_cli.Settings.from_env", return_value=settings), patch(
            "cela_sh.learning_cli.create_planner", return_value=planner
        ), redirect_stdout(io.StringIO()):
            self.assertEqual(main(["search", "--scope", "both", "--session-id", "project-a", "--tag", "testing"]), 0)

        self.assertEqual(
            store.list_memory.call_args_list,
            [
                call("project-a", limit=20, tags=["testing"], memory_type=None),
                call(GLOBAL_MEMORY_SESSION_ID, limit=20, tags=["testing"], memory_type=None),
            ],
        )


if __name__ == "__main__":
    unittest.main()
