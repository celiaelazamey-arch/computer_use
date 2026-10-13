"""Tests for project decision memory: structured decisions, plan capture, and briefs."""

import io
import json
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime
from hashlib import sha256
from pathlib import Path
from unittest.mock import Mock, patch

from cela_sh.cli import main
from cela_sh.memory import MemoryStoreError
from cela_sh.memory.metadata import (
    DECISION_STATUSES,
    MEMORY_TYPES,
    normalize_decision_status,
    normalize_memory_type,
)
from cela_sh.settings import ConfigurationError, Settings

SESSION = "vscode-ai-agent"

PLAN_JSON = json.dumps(
    {
        "analysis": "Use a retry queue and avoid in-process state for flaky browser sessions.",
        "steps": [
            {
                "action": "OPEN_APP",
                "target": "Terminal",
                "value": "",
                "reason": "Open the project",
                "risk": "low",
                "requires_confirmation": False,
                "policy_reasons": [],
            },
            {
                "action": "STOP",
                "target": "done",
                "value": "",
                "reason": "Finish",
                "risk": "low",
                "requires_confirmation": False,
                "policy_reasons": [],
            },
        ],
        "status": "ready",
        "requires_confirmation": False,
        "dry_run": True,
        "execution_enabled": False,
    }
)


def assert_recorded_at(value: object) -> None:
    try:
        datetime.fromisoformat(str(value))
    except (TypeError, ValueError) as exc:
        raise AssertionError(f"recorded_at is not ISO-8601: {value!r}") from exc


class DecisionMetadataTests(unittest.TestCase):
    def test_new_types_are_supported(self):
        for memory_type in ("decision", "failure", "debt"):
            self.assertIn(memory_type, MEMORY_TYPES)
            self.assertEqual(normalize_memory_type(memory_type), memory_type)

    def test_decision_statuses(self):
        self.assertEqual(tuple(DECISION_STATUSES), ("active", "superseded", "deprecated", "rejected"))
        self.assertEqual(normalize_decision_status(None), "active")
        for status in DECISION_STATUSES:
            self.assertEqual(normalize_decision_status(status), status)
        with self.assertRaises(ValueError):
            normalize_decision_status("stale")

    def test_unknown_types_still_rejected(self):
        with self.assertRaises(ValueError):
            normalize_memory_type("opinion")


class DecisionLearningTests(unittest.TestCase):
    def make_store(self):
        store = Mock()
        store.add_memory.return_value = True
        planner = Mock(memory_store=store)
        settings = Settings(memory_backend="sqlite-vec")
        return settings, store, planner

    def run_learn(self, arguments):
        settings, store, planner = self.make_store()
        stdout = io.StringIO()
        stderr = io.StringIO()
        with patch("cela_sh.learning_cli.Settings.from_env", return_value=settings), patch(
            "cela_sh.learning_cli.create_planner", return_value=planner
        ), redirect_stdout(stdout), redirect_stderr(stderr):
            exit_code = main(["learn", *arguments])
        return exit_code, store, json.loads(stdout.getvalue()) if stdout.getvalue() else None, stderr.getvalue()

    def test_decision_requires_a_reason(self):
        stderr = io.StringIO()
        with redirect_stderr(stderr):
            with self.assertRaises(SystemExit) as raised:
                main(["learn", "--session-id", SESSION, "--type", "decision", "--content", "Adopt Redis."])
        self.assertEqual(raised.exception.code, 2)
        self.assertEqual(json.loads(stderr.getvalue())["error"]["code"], "cli_usage_error")
        self.assertIn("--reason", json.loads(stderr.getvalue())["error"]["message"])

    def test_decision_stores_rationale_alternatives_and_status(self):
        exit_code, store, output, _ = self.run_learn(
            [
                "--session-id",
                SESSION,
                "--type",
                "decision",
                "--content",
                "Adopt Redis for the ephemeral cache.",
                "--reason",
                "SQLite writes blocked the agent loop under concurrent load.",
                "--rejected",
                "Store sessions in SQLite: locking under concurrency.",
                "--rejected",
                "In-process dict: lost on restart.",
                "--tag",
                "storage",
            ]
        )
        self.assertEqual(exit_code, 0)
        stored_session, stored_content, stored_metadata = store.add_memory.call_args.args
        self.assertEqual(stored_session, SESSION)
        self.assertEqual(stored_content, "Adopt Redis for the ephemeral cache.")
        self.assertEqual(stored_metadata["type"], "decision")
        self.assertEqual(stored_metadata["status"], "active")
        self.assertEqual(
            stored_metadata["rationale"],
            "SQLite writes blocked the agent loop under concurrent load.",
        )
        self.assertEqual(
            stored_metadata["rejected_alternatives"],
            [
                "Store sessions in SQLite: locking under concurrency.",
                "In-process dict: lost on restart.",
            ],
        )
        self.assertNotIn("superseded_by", stored_metadata)
        self.assertEqual(stored_metadata["tags"], ["storage"])
        assert_recorded_at(stored_metadata["recorded_at"])
        self.assertEqual(output["status"], "active")

    def test_superseded_decision_requires_its_successor(self):
        stderr = io.StringIO()
        with redirect_stderr(stderr):
            with self.assertRaises(SystemExit) as raised:
                main(
                    [
                        "learn",
                        "--session-id",
                        SESSION,
                        "--type",
                        "decision",
                        "--status",
                        "superseded",
                        "--content",
                        "Store sessions in SQLite.",
                        "--reason",
                        "Simplest at project start.",
                    ]
                )
        self.assertEqual(raised.exception.code, 2)
        self.assertIn("--superseded-by", json.loads(stderr.getvalue())["error"]["message"])

    def test_superseded_decision_is_stored_with_its_successor(self):
        exit_code, store, output, _ = self.run_learn(
            [
                "--session-id",
                SESSION,
                "--type",
                "decision",
                "--status",
                "superseded",
                "--superseded-by",
                "Adopt Redis for the ephemeral cache.",
                "--content",
                "Store sessions in SQLite.",
                "--reason",
                "Simplest at project start.",
            ]
        )
        self.assertEqual(exit_code, 0)
        _, _, stored_metadata = store.add_memory.call_args.args
        self.assertEqual(stored_metadata["status"], "superseded")
        self.assertEqual(stored_metadata["superseded_by"], "Adopt Redis for the ephemeral cache.")
        self.assertEqual(output["status"], "superseded")

    def test_superseded_by_is_rejected_outside_superseded_status(self):
        stderr = io.StringIO()
        with redirect_stderr(stderr):
            with self.assertRaises(SystemExit) as raised:
                main(
                    [
                        "learn",
                        "--session-id",
                        SESSION,
                        "--type",
                        "decision",
                        "--superseded-by",
                        "something",
                        "--content",
                        "Adopt Redis.",
                        "--reason",
                        "speed",
                    ]
                )
        self.assertEqual(raised.exception.code, 2)
        self.assertEqual(json.loads(stderr.getvalue())["error"]["code"], "cli_usage_error")

    def test_more_than_ten_rejected_alternatives_are_rejected(self):
        arguments = [
            "--session-id",
            SESSION,
            "--type",
            "decision",
            "--content",
            "Decision.",
            "--reason",
            "because",
        ]
        for index in range(11):
            arguments += ["--rejected", f"alternative {index}"]
        stderr = io.StringIO()
        with redirect_stderr(stderr):
            with self.assertRaises(SystemExit) as raised:
                main(["learn", *arguments])
        self.assertEqual(raised.exception.code, 2)

    def test_failure_stores_optional_cause(self):
        exit_code, store, output, _ = self.run_learn(
            [
                "--session-id",
                SESSION,
                "--type",
                "failure",
                "--content",
                "Browser Run sessions expired mid-plan on the free tier.",
                "--why",
                "The 30 second session budget ran out before the second navigation.",
            ]
        )
        self.assertEqual(exit_code, 0)
        _, _, stored_metadata = store.add_memory.call_args.args
        self.assertEqual(stored_metadata["type"], "failure")
        self.assertEqual(
            stored_metadata["why"],
            "The 30 second session budget ran out before the second navigation.",
        )
        assert_recorded_at(stored_metadata["recorded_at"])
        self.assertNotIn("status", output)

    def test_failure_without_a_cause_is_plain(self):
        exit_code, store, _, _ = self.run_learn(
            ["--session-id", SESSION, "--type", "failure", "--content", "Model timed out during planning."]
        )
        self.assertEqual(exit_code, 0)
        _, _, stored_metadata = store.add_memory.call_args.args
        self.assertNotIn("why", stored_metadata)

    def test_debt_is_stored_plain(self):
        exit_code, store, output, _ = self.run_learn(
            ["--session-id", SESSION, "--type", "debt", "--content", "Retrieval has no reranking pass."]
        )
        self.assertEqual(exit_code, 0)
        _, _, stored_metadata = store.add_memory.call_args.args
        self.assertEqual(stored_metadata["type"], "debt")
        assert_recorded_at(stored_metadata["recorded_at"])
        self.assertNotIn("status", output)

    def test_decision_options_are_rejected_for_other_types(self):
        for arguments in (
            ["--session-id", SESSION, "--type", "note", "--content", "fact", "--reason", "nope"],
            ["--session-id", SESSION, "--type", "note", "--content", "fact", "--status", "superseded"],
            ["--session-id", SESSION, "--type", "debt", "--content", "debt", "--why", "nope"],
            ["--session-id", SESSION, "--type", "decision", "--content", "decision", "--reason", "ok", "--why", "nope"],
        ):
            with self.subTest(arguments=arguments):
                stderr = io.StringIO()
                with redirect_stderr(stderr):
                    with self.assertRaises(SystemExit) as raised:
                        main(["learn", *arguments])
                self.assertEqual(raised.exception.code, 2)
                self.assertEqual(json.loads(stderr.getvalue())["error"]["code"], "cli_usage_error")


class PlanCaptureTests(unittest.TestCase):
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

    def test_plan_file_is_captured_as_an_active_decision_by_source(self):
        settings, store, planner = self.make_store()
        stdout = io.StringIO()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "approved-plan.json"
            path.write_text(PLAN_JSON, encoding="utf-8")
            with patch("cela_sh.learning_cli.Settings.from_env", return_value=settings), patch(
                "cela_sh.learning_cli.create_planner", return_value=planner
            ), redirect_stdout(stdout):
                exit_code = main(["learn", "--session-id", SESSION, "--from-plan", str(path), "--tag", "plan"])

        self.assertEqual(exit_code, 0)
        store.add_memory.assert_not_called()
        session, source_path, source_hash, records = store.replace_source.call_args.args
        self.assertEqual(session, SESSION)
        self.assertTrue(source_path.startswith("external/"))
        self.assertEqual(len(source_hash), 64)
        self.assertEqual(source_hash, sha256(PLAN_JSON.encode("utf-8")).hexdigest())
        self.assertEqual(len(records), 1)
        content, metadata = records[0]
        self.assertEqual(content, "Use a retry queue and avoid in-process state for flaky browser sessions.")
        self.assertEqual(metadata["type"], "decision")
        self.assertEqual(metadata["status"], "active")
        self.assertEqual(metadata["chunk_index"], 1)
        self.assertEqual(metadata["chunk_count"], 1)
        self.assertEqual(metadata["source_path"], source_path)
        self.assertEqual(metadata["source_hash"], source_hash)
        self.assertEqual(metadata["source_name"], "approved-plan.json")
        self.assertEqual(metadata["source"], "explicit_cli_plan_learning")
        self.assertEqual(metadata["tags"], ["plan"])
        self.assertEqual(metadata["content_hash"], sha256(content.encode("utf-8")).hexdigest())
        self.assertIn("approved-plan.json", metadata["rationale"])
        self.assertIn("2 steps", metadata["rationale"])
        assert_recorded_at(metadata["recorded_at"])
        output = json.loads(stdout.getvalue())
        self.assertEqual(output["status"], "imported")
        self.assertEqual(output["type"], "decision")
        self.assertEqual(output["decision_status"], "active")
        self.assertEqual(output["steps"], 2)
        self.assertTrue(output["learned"])

    def test_reimport_of_an_unchanged_plan_reports_no_changes(self):
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
            path = Path(directory) / "approved-plan.json"
            path.write_text(PLAN_JSON, encoding="utf-8")
            with patch("cela_sh.learning_cli.Settings.from_env", return_value=settings), patch(
                "cela_sh.learning_cli.create_planner", return_value=planner
            ), redirect_stdout(stdout):
                exit_code = main(["learn", "--session-id", SESSION, "--from-plan", str(path)])

        self.assertEqual(exit_code, 0)
        output = json.loads(stdout.getvalue())
        self.assertEqual(output["status"], "unchanged")
        self.assertFalse(output["learned"])
        self.assertFalse(output["metadata_applied"])

    def test_invalid_plan_file_is_rejected_before_any_store_touch(self):
        settings, store, planner = self.make_store()
        stderr = io.StringIO()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "garbage.json"
            path.write_text('{"analysis": "incomplete"}', encoding="utf-8")
            with patch("cela_sh.learning_cli.Settings.from_env", return_value=settings), patch(
                "cela_sh.learning_cli.create_planner", return_value=planner
            ), redirect_stderr(stderr):
                exit_code = main(["learn", "--session-id", SESSION, "--from-plan", str(path)])

        self.assertEqual(exit_code, 2)
        self.assertEqual(json.loads(stderr.getvalue())["error"]["code"], "configuration_error")
        self.assertIn("not a valid CELA plan JSON", json.loads(stderr.getvalue())["error"]["message"])
        store.replace_source.assert_not_called()
        store.add_memory.assert_not_called()

    def test_missing_plan_file_is_rejected(self):
        stderr = io.StringIO()
        with patch("cela_sh.learning_cli.Settings.from_env", return_value=Settings(memory_backend="sqlite-vec")), redirect_stderr(
            stderr
        ):
            exit_code = main(["learn", "--session-id", SESSION, "--from-plan", "/nonexistent/plan.json"])
        self.assertEqual(exit_code, 2)
        self.assertEqual(json.loads(stderr.getvalue())["error"]["code"], "configuration_error")

    def test_from_plan_requires_the_decision_type(self):
        for arguments in (
            ["--session-id", SESSION, "--type", "note", "--from-plan", "plan.json"],
            ["--session-id", SESSION, "--from-plan", "plan.json", "--reason", "extra"],
            ["--session-id", SESSION, "--from-plan", "plan.json", "--status", "superseded"],
            ["--session-id", SESSION, "--from-plan", "plan.json", "--superseded-by", "x"],
        ):
            with self.subTest(arguments=arguments):
                stderr = io.StringIO()
                with redirect_stderr(stderr):
                    with self.assertRaises(SystemExit) as raised:
                        main(["learn", *arguments])
                self.assertEqual(raised.exception.code, 2)
                self.assertEqual(json.loads(stderr.getvalue())["error"]["code"], "cli_usage_error")

    def test_content_and_from_plan_are_mutually_exclusive(self):
        stderr = io.StringIO()
        with redirect_stderr(stderr):
            with self.assertRaises(SystemExit) as raised:
                main(
                    [
                        "learn",
                        "--session-id",
                        SESSION,
                        "--content",
                        "text",
                        "--from-plan",
                        "plan.json",
                    ]
                )
        self.assertEqual(raised.exception.code, 2)
        self.assertEqual(json.loads(stderr.getvalue())["error"]["code"], "cli_usage_error")


def _record(content, metadata, session=SESSION, score=0.0):
    return {"session_id": session, "content": content, "metadata": metadata, "score": score}


def make_brief_store_data():
    goals = [
        _record(
            "A development agent that lives inside VS Code and keeps project context across sessions.",
            {"scope": "project", "type": "note", "tags": ["goal"], "source": "explicit_cli_learning", "recorded_at": "2026-10-12T10:00:00+00:00"},
        ),
        _record(
            "An older, stale goal statement.",
            {"scope": "project", "type": "note", "tags": ["goal"], "source": "explicit_cli_learning", "recorded_at": "2026-09-01T09:00:00+00:00"},
        ),
    ]
    decisions = [
        _record(
            "Adopt Redis for the ephemeral cache.",
            {
                "scope": "project",
                "type": "decision",
                "tags": ["storage"],
                "source": "explicit_cli_learning",
                "recorded_at": "2026-10-05T08:30:00+00:00",
                "rationale": "SQLite writes blocked the agent loop under concurrent load.",
                "rejected_alternatives": ["Store sessions in SQLite: locking under concurrency."],
                "status": "active",
            },
        ),
        _record(
            "Store sessions in SQLite.",
            {
                "scope": "project",
                "type": "decision",
                "tags": ["storage"],
                "source": "explicit_cli_learning",
                "recorded_at": "2026-08-01T08:00:00+00:00",
                "rationale": "Simplest at project start.",
                "rejected_alternatives": [],
                "status": "superseded",
                "superseded_by": "Adopt Redis for the ephemeral cache.",
            },
        ),
        _record(
            "Ship the Qdrant importer in the first release.",
            {
                "scope": "project",
                "type": "decision",
                "tags": [],
                "source": "explicit_cli_learning",
                "recorded_at": "2026-07-15T12:00:00+00:00",
                "rationale": "Scope reduction after the July review.",
                "rejected_alternatives": [],
                "status": "deprecated",
            },
        ),
        _record(
            "Legacy decision recorded before statuses existed.",
            {"scope": "project", "type": "decision", "tags": [], "source": "explicit_cli_learning"},
        ),
    ]
    failures = [
        _record(
            "Browser Run sessions expired mid-plan on the free tier.",
            {
                "scope": "project",
                "type": "failure",
                "tags": [],
                "source": "explicit_cli_learning",
                "recorded_at": "2026-09-20T16:00:00+00:00",
                "why": "The 30 second session budget ran out before the second navigation.",
            },
        )
    ]
    debt = [
        _record(
            "Retrieval has no reranking pass.",
            {"scope": "project", "type": "debt", "tags": [], "source": "explicit_cli_learning", "recorded_at": "2026-09-02T11:00:00+00:00"},
        )
    ]
    nexts = [
        _record(
            "Wire the decision log into the planning prompt as a first-class section.",
            {"scope": "project", "type": "note", "tags": ["next"], "source": "explicit_cli_learning", "recorded_at": "2026-10-10T07:00:00+00:00"},
        )
    ]
    return {"goal": goals, "decision": decisions, "failure": failures, "debt": debt, "next": nexts}


class BriefTests(unittest.TestCase):
    def make_brief_run(self, data=None, text=False):
        store = Mock()
        data = data if data is not None else make_brief_store_data()

        def list_memory(session_id, limit=100, *, tags=None, memory_type=None):
            if tags == ["goal"]:
                return data["goal"]
            if memory_type == "decision":
                return data["decision"]
            if memory_type == "failure":
                return data["failure"]
            if memory_type == "debt":
                return data["debt"]
            if tags == ["next"]:
                return data["next"]
            return []

        store.list_memory.side_effect = list_memory
        planner = Mock(memory_store=store)
        settings = Settings(memory_backend="sqlite-vec")
        stdout = io.StringIO()
        stderr = io.StringIO()
        arguments = ["brief", "--session-id", SESSION]
        if text:
            arguments.append("--text")
        with patch("cela_sh.learning_cli.Settings.from_env", return_value=settings), patch(
            "cela_sh.learning_cli.create_planner", return_value=planner
        ), redirect_stdout(stdout), redirect_stderr(stderr):
            exit_code = main(arguments)
        return exit_code, store, stdout.getvalue(), stderr.getvalue()

    def test_brief_assembles_active_and_closed_decisions(self):
        exit_code, _, stdout_text, _ = self.make_brief_run()
        self.assertEqual(exit_code, 0)
        brief = json.loads(stdout_text)
        self.assertEqual(brief["scope"], "project")
        self.assertEqual(brief["session_id"], SESSION)
        self.assertTrue(brief["has_context"])
        self.assertEqual(
            brief["project_goal"],
            "A development agent that lives inside VS Code and keeps project context across sessions.",
        )
        self.assertEqual(len(brief["active_decisions"]), 2)
        self.assertEqual(brief["active_decisions"][0]["content"], "Adopt Redis for the ephemeral cache.")
        self.assertEqual(
            brief["active_decisions"][0]["rationale"],
            "SQLite writes blocked the agent loop under concurrent load.",
        )
        self.assertEqual(
            brief["active_decisions"][0]["rejected_alternatives"],
            ["Store sessions in SQLite: locking under concurrency."],
        )
        self.assertEqual(
            brief["active_decisions"][0]["recorded_at"],
            "2026-10-05T08:30:00+00:00",
        )
        self.assertEqual(brief["active_decisions"][1]["content"], "Legacy decision recorded before statuses existed.")
        self.assertIsNone(brief["active_decisions"][1]["rationale"])
        self.assertEqual(len(brief["closed_decisions"]), 2)
        self.assertEqual(brief["closed_decisions"][0]["content"], "Store sessions in SQLite.")
        self.assertEqual(brief["closed_decisions"][0]["status"], "superseded")
        self.assertEqual(
            brief["closed_decisions"][0]["superseded_by"],
            "Adopt Redis for the ephemeral cache.",
        )
        self.assertEqual(brief["closed_decisions"][1]["status"], "deprecated")
        self.assertEqual(brief["failures"][0]["content"], "Browser Run sessions expired mid-plan on the free tier.")
        self.assertEqual(
            brief["failures"][0]["why"],
            "The 30 second session budget ran out before the second navigation.",
        )
        self.assertEqual(brief["technical_debt"][0]["content"], "Retrieval has no reranking pass.")
        self.assertEqual(
            brief["next_steps"][0]["content"],
            "Wire the decision log into the planning prompt as a first-class section.",
        )
        self.assertEqual(
            brief["counts"],
            {
                "active_decisions": 2,
                "closed_decisions": 2,
                "failures": 1,
                "technical_debt": 1,
                "next_steps": 1,
            },
        )
        self.assertEqual(brief["memory_range"]["oldest_recorded_at"], "2026-07-15T12:00:00+00:00")
        self.assertEqual(brief["memory_range"]["newest_recorded_at"], "2026-10-12T10:00:00+00:00")
        assert_recorded_at(brief["generated_at"])

    def test_brief_reads_only_the_project_session(self):
        exit_code, store, _, _ = self.make_brief_run()
        self.assertEqual(exit_code, 0)
        for call in store.list_memory.call_args_list:
            self.assertEqual(call.args[0], SESSION)

    def test_brief_empty_store_reports_no_context_honestly(self):
        data = {"goal": [], "decision": [], "failure": [], "debt": [], "next": []}
        exit_code, _, stdout_text, stderr_text = self.make_brief_run(data=data)
        self.assertEqual(exit_code, 0)
        self.assertEqual(stderr_text, "")
        brief = json.loads(stdout_text)
        self.assertFalse(brief["has_context"])
        self.assertIsNone(brief["project_goal"])
        self.assertEqual(brief["active_decisions"], [])
        self.assertEqual(brief["closed_decisions"], [])
        self.assertEqual(brief["failures"], [])
        self.assertEqual(brief["technical_debt"], [])
        self.assertEqual(brief["next_steps"], [])
        self.assertEqual(brief["memory_range"], {"oldest_recorded_at": None, "newest_recorded_at": None})

    def test_brief_text_output_is_human_readable(self):
        exit_code, _, stdout_text, _ = self.make_brief_run(text=True)
        self.assertEqual(exit_code, 0)
        self.assertIn("PROJECT BRIEF — vscode-ai-agent", stdout_text)
        self.assertIn("ACTIVE DECISIONS (2)", stdout_text)
        self.assertIn("CLOSED DECISIONS (2)", stdout_text)
        self.assertIn("PAST FAILURES (1)", stdout_text)
        self.assertIn("TECHNICAL DEBT (1)", stdout_text)
        self.assertIn("NEXT STEPS (1)", stdout_text)
        self.assertIn("why: SQLite writes blocked the agent loop under concurrent load.", stdout_text)
        self.assertIn("rejected: Store sessions in SQLite: locking under concurrency.", stdout_text)
        self.assertIn("[superseded] Store sessions in SQLite. (superseded by: Adopt Redis for the ephemeral cache.)", stdout_text)
        self.assertIn("cause: The 30 second session budget ran out before the second navigation.", stdout_text)
        self.assertIn("MEMORY RANGE: 2026-07-15T12:00:00+00:00 .. 2026-10-12T10:00:00+00:00", stdout_text)

    def test_brief_empty_text_output_includes_onboarding_hint(self):
        data = {"goal": [], "decision": [], "failure": [], "debt": [], "next": []}
        exit_code, _, stdout_text, _ = self.make_brief_run(data=data, text=True)
        self.assertEqual(exit_code, 0)
        self.assertIn("No project memory yet.", stdout_text)
        self.assertIn("--type decision", stdout_text)

    def test_brief_requires_a_session_id(self):
        stderr = io.StringIO()
        with redirect_stderr(stderr):
            with self.assertRaises(SystemExit) as raised:
                main(["brief"])
        self.assertEqual(raised.exception.code, 2)
        self.assertEqual(json.loads(stderr.getvalue())["error"]["code"], "cli_usage_error")

    def test_brief_requires_an_enabled_memory_backend(self):
        planner = Mock(memory_store=None)
        stderr = io.StringIO()
        with patch("cela_sh.learning_cli.Settings.from_env", return_value=Settings()), patch(
            "cela_sh.learning_cli.create_planner", return_value=planner
        ) as factory, redirect_stderr(stderr):
            exit_code = main(["brief", "--session-id", SESSION])
        self.assertEqual(exit_code, 2)
        self.assertEqual(json.loads(stderr.getvalue())["error"]["code"], "configuration_error")
        factory.assert_not_called()

    def test_brief_rejects_an_invalid_stored_status(self):
        data = make_brief_store_data()
        data["decision"][0]["metadata"]["status"] = "stale"
        exit_code, _, _, stderr_text = self.make_brief_run(data=data)
        self.assertEqual(exit_code, 6)
        self.assertEqual(json.loads(stderr_text)["error"]["code"], "planner_memory_error")

    def test_brief_rejects_invalid_records(self):
        data = make_brief_store_data()
        data["failure"][0]["content"] = 42
        exit_code, _, _, stderr_text = self.make_brief_run(data=data)
        self.assertEqual(exit_code, 6)
        self.assertEqual(json.loads(stderr_text)["error"]["code"], "planner_memory_error")

    def test_brief_rejects_invalid_rejected_alternatives(self):
        data = make_brief_store_data()
        data["decision"][0]["metadata"]["rejected_alternatives"] = "not-a-list"
        exit_code, _, _, stderr_text = self.make_brief_run(data=data)
        self.assertEqual(exit_code, 6)
        self.assertEqual(json.loads(stderr_text)["error"]["code"], "planner_memory_error")

    def test_brief_store_failure_uses_the_memory_error_contract(self):
        store = Mock()
        store.list_memory.side_effect = MemoryStoreError("storage offline")
        planner = Mock(memory_store=store)
        stderr = io.StringIO()
        with patch("cela_sh.learning_cli.Settings.from_env", return_value=Settings(memory_backend="sqlite-vec")), patch(
            "cela_sh.learning_cli.create_planner", return_value=planner
        ), redirect_stderr(stderr):
            exit_code = main(["brief", "--session-id", SESSION])
        self.assertEqual(exit_code, 6)
        self.assertEqual(json.loads(stderr.getvalue())["error"]["code"], "planner_memory_error")


class BriefWithoutModelTests(unittest.TestCase):
    def test_brief_works_with_a_real_empty_store_and_never_calls_the_model(self):
        from cela_sh.memory import SqliteVecMemoryStore

        class RefusingEmbedder:
            def embed(self, text):
                raise AssertionError("brief must not call the embedding provider")

        store = SqliteVecMemoryStore(":memory:", RefusingEmbedder())
        planner = Mock(memory_store=store)
        settings = Settings(memory_backend="sqlite-vec", sqlite_vec_path=":memory:")
        stdout = io.StringIO()
        with patch("cela_sh.learning_cli.Settings.from_env", return_value=settings), patch(
            "cela_sh.learning_cli.create_planner", return_value=planner
        ), redirect_stdout(stdout):
            exit_code = main(["brief", "--session-id", SESSION])

        self.assertEqual(exit_code, 0)
        brief = json.loads(stdout.getvalue())
        self.assertFalse(brief["has_context"])
        self.assertIsNone(brief["project_goal"])


if __name__ == "__main__":
    unittest.main()
