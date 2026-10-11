"""Fails when the repository's public contract drifts from the recorded snapshot.

The snapshot in ``tests/snapshots/api_surface.json`` is reviewed like code. These
tests make the review unavoidable: a removal, rename, signature change, CLI flag
change, exit-code change, or Worker route change cannot reach ``main`` silently.

After an *intentional and backwards compatible* change, record it with::

    bash ./cela.sh api-surface --update

and let the diff be reviewed in the pull request.
"""

import importlib.util
import json
import sys
import unittest
from difflib import unified_diff
from pathlib import Path
from typing import Any

TESTS = Path(__file__).resolve().parent
ROOT = TESTS.parent
sys.path.insert(0, str(TESTS))


def load_tool() -> Any:
    """Import ``tools/api_surface.py`` without adding ``tools`` to sys.path."""
    path = ROOT / "tools" / "api_surface.py"
    spec = importlib.util.spec_from_file_location("cela_api_surface", path)
    if spec is None or spec.loader is None:  # pragma: no cover - defensive
        raise ImportError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


TOOL = load_tool()
SNAPSHOT_PATH = TOOL.SNAPSHOT_PATH
MAX_DIFF_LINES = 80


def load_snapshot() -> dict[str, Any]:
    return json.loads(SNAPSHOT_PATH.read_text(encoding="utf-8"))


def diff_text(recorded: dict[str, Any], current: dict[str, Any]) -> str:
    lines = list(
        unified_diff(
            json.dumps(recorded, indent=2, sort_keys=True, ensure_ascii=False).splitlines(),
            json.dumps(current, indent=2, sort_keys=True, ensure_ascii=False).splitlines(),
            fromfile="recorded",
            tofile="live",
            lineterm="",
        )
    )
    truncated = lines[:MAX_DIFF_LINES]
    if len(lines) > MAX_DIFF_LINES:
        truncated.append(f"... {len(lines) - MAX_DIFF_LINES} more diff lines")
    return "\n".join(truncated)


class SnapshotPresenceTests(unittest.TestCase):
    def test_the_snapshot_is_committed_and_parses(self):
        self.assertTrue(SNAPSHOT_PATH.is_file(), f"missing {SNAPSHOT_PATH}; run: bash ./cela.sh api-surface --update")
        self.assertIsInstance(load_snapshot(), dict)

    def test_the_snapshot_is_deterministic_across_runs(self):
        first = TOOL.build_surface()
        second = TOOL.build_surface()
        self.assertEqual(first, second, "the surface must not depend on hash order or object identity")

    def test_the_snapshot_records_both_applications(self):
        recorded = load_snapshot()
        self.assertEqual(recorded["schema_version"], TOOL.SCHEMA_VERSION)
        self.assertIn("python", recorded)
        self.assertIn("worker", recorded)
        self.assertTrue(recorded["worker"]["routes"], "the Worker route table must not be empty")


class DriftTests(unittest.TestCase):
    def test_the_live_surface_matches_the_recorded_snapshot(self):
        recorded = load_snapshot()
        current = TOOL.build_surface()
        if recorded != current:
            notes = "\n".join(f"  - {note}" for note in TOOL.summarise_drift(recorded, current))
            self.fail(
                "The public contract surface drifted.\n"
                f"{notes}\n\n"
                f"{diff_text(recorded, current)}\n\n"
                "If this change is intentional and backwards compatible, record it with:\n"
                "    bash ./cela.sh api-surface --update\n"
                "and explain it in the pull request."
            )

    def test_no_public_name_was_removed_or_renamed(self):
        recorded = set(load_snapshot()["python"]["exports"])
        current = set(TOOL.build_surface()["python"]["exports"])
        self.assertEqual(
            sorted(recorded - current),
            [],
            "Removing a public name breaks every caller. Keep it, or deprecate it in a "
            "documented release with an alias.",
        )

    def test_no_cli_flag_or_exit_code_changed(self):
        recorded = load_snapshot()["python"]
        current = TOOL.build_surface()["python"]
        recorded_flags = {tuple(flag["options"]) for flag in recorded["cli_flags"]}
        current_flags = {tuple(flag["options"]) for flag in current["cli_flags"]}
        self.assertEqual(sorted(recorded_flags - current_flags), [], "a CLI flag was removed")
        self.assertEqual(recorded["exit_codes"], current["exit_codes"], "the exit-code table changed")

    def test_no_worker_route_was_removed(self):
        recorded = {(route["method"], route["path"]) for route in load_snapshot()["worker"]["routes"]}
        current = {(route["method"], route["path"]) for route in TOOL.build_surface()["worker"]["routes"]}
        self.assertEqual(sorted(recorded - current), [], "a Worker route disappeared")


class LockedInvariantTests(unittest.TestCase):
    """Properties that must hold even if someone regenerates the snapshot blindly."""

    def test_the_snapshot_locks_the_dry_run_invariants(self):
        fields = load_snapshot()["python"]["exports"]["Plan"]["fields"]
        self.assertEqual(fields["dry_run"]["annotation"], "typing.Literal[True]")
        self.assertEqual(fields["execution_enabled"]["annotation"], "typing.Literal[False]")

    def test_the_snapshot_locks_the_action_allowlist(self):
        members = load_snapshot()["python"]["exports"]["Action"]["members"]
        self.assertEqual(
            sorted(members),
            [
                "ASK_USER",
                "CLICK",
                "NAVIGATE",
                "OBSERVE",
                "OPEN_APP",
                "SCROLL",
                "SEARCH_WEB",
                "STOP",
                "TYPE",
                "WAIT",
            ],
            "Adding an action widens what the planner may propose; it needs an explicit "
            "policy rule and a documented review, not a snapshot refresh.",
        )

    def test_the_snapshot_records_only_the_read_only_worker_routes(self):
        routes = {(route["method"], route["path"]) for route in load_snapshot()["worker"]["routes"]}
        self.assertEqual(
            routes,
            {
                ("GET", "/api/session"),
                ("GET", "/api/status"),
                ("GET", "/health"),
                ("POST", "/api/execute"),
                ("POST", "/api/login"),
                ("POST", "/api/logout"),
                ("POST", "/api/plan"),
            },
        )
        self.assertEqual(load_snapshot()["worker"]["route_prefixes"], ["/api/"])

    def test_memory_stays_disabled_by_default(self):
        settings = load_snapshot()["python"]["exports"]["Settings"]
        self.assertEqual(settings["fields"]["memory_backend"]["annotation"], "str")
        self.assertIn("memory_backend: str = 'disabled'", settings["init"])


class DriftSummaryTests(unittest.TestCase):
    """The guidance shown to a contributor has to stay accurate."""

    def test_the_summary_labels_removals_additions_and_changes(self):
        recorded = {
            "python": {
                "exports": {"Settings": {"kind": "class"}, "Gone": {"kind": "class"}},
                "cli_flags": [{"options": ["--goal"], "required": True, "default": "None", "type": None, "nargs": None}],
                "exit_codes": {"ok": 0},
                "memory_backends": ["disabled", "qdrant"],
            },
            "worker": {"routes": [{"method": "GET", "path": "/health"}]},
        }
        current = {
            "python": {
                "exports": {"Settings": {"kind": "callable"}, "Added": {"kind": "class"}},
                "cli_flags": [{"options": ["--target"], "required": True, "default": "None", "type": None, "nargs": None}],
                "exit_codes": {"ok": 1},
                "memory_backends": ["disabled"],
            },
            "worker": {"routes": [{"method": "POST", "path": "/api/admin"}]},
        }

        notes = TOOL.summarise_drift(recorded, current)

        self.assertIn("REMOVED public name: Gone", notes)
        self.assertIn("ADDED public name: Added", notes)
        self.assertIn("CHANGED public name: Settings", notes)
        self.assertIn("REMOVED CLI flag: --goal", notes)
        self.assertIn("ADDED CLI flag: --target", notes)
        self.assertIn("CHANGED exit code: ok 0 -> 1", notes)
        self.assertIn("REMOVED memory backend: qdrant", notes)
        self.assertIn("REMOVED Worker route: GET /health", notes)
        self.assertIn("ADDED Worker route: POST /api/admin", notes)

    def test_an_unchanged_surface_produces_no_notes(self):
        surface = TOOL.build_surface()
        self.assertEqual(TOOL.summarise_drift(surface, surface), [])


if __name__ == "__main__":
    unittest.main()
