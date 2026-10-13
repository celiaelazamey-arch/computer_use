#!/usr/bin/env python3
"""Record the repository's public contract surface as a reviewable snapshot.

This is the mechanical half of the extension safety net. Extending the
repository is expected and welcome; *silently* changing what other code, scripts,
or deployments already rely on is not. The snapshot freezes the parts that are
contracts rather than implementation:

* every name exported from ``cela_sh`` and its subpackages, with signatures,
  Enum members, and Pydantic model fields (including the ``Literal`` locks that
  keep plans ``dry_run=true`` and ``execution_enabled=false``);
* the CLI flag surfaces (including the explicit-learning subcommand) and exit-code table;
* the registered memory backend names;
* the Worker's exported symbols and HTTP route table.

``tests/test_api_surface.py`` fails when the live surface drifts from the
snapshot, so an accidental removal or rename cannot reach ``main``. Additions are
allowed but still require an explicit, reviewable snapshot update.

Usage:
    python tools/api_surface.py --check    # exit 1 when the surface drifted
    python tools/api_surface.py --update   # rewrite the snapshot on purpose
    python tools/api_surface.py            # print the surface as JSON
"""

from __future__ import annotations

import argparse
import importlib
import inspect
import json
import re
import sys
from dataclasses import MISSING, fields as dataclass_fields, is_dataclass
from enum import Enum
from pathlib import Path
from types import GenericAlias, UnionType
from typing import Any, get_origin

ROOT = Path(__file__).resolve().parents[1]
SNAPSHOT_PATH = ROOT / "tests" / "snapshots" / "api_surface.json"
WORKER_SOURCE = ROOT / "ccelia2027" / "src" / "index.js"

SCHEMA_VERSION = 1
PYTHON_PACKAGES = ("cela_sh", "cela_sh.memory", "cela_sh.planner")
WORKER_ROUTE = re.compile(r'url\.pathname === "([^"]+)"\s*&&\s*request\.method === "([A-Z]+)"')
WORKER_ROUTE_PREFIX = re.compile(r'url\.pathname\.startsWith\("([^"]+)"\)')
WORKER_FUNCTION = re.compile(r"^export\s+(?:async\s+)?function\s+(\w+)", re.MULTILINE)
WORKER_CLASS = re.compile(r"^export\s+class\s+(\w+)", re.MULTILINE)
WORKER_DEFAULT = re.compile(r"^export\s+default\b", re.MULTILINE)


def _signature(target: Any) -> str | None:
    """A stable, comparable signature string, or None when introspection fails."""
    try:
        return str(inspect.signature(target))
    except (TypeError, ValueError):
        return None


def _annotation_text(value: Any) -> str | None:
    """A stable name for an annotation or callable.

    Typing constructs keep their full text (``typing.Literal[True]``,
    ``list[PlannedStep]``) because that text *is* part of the contract, while
    plain classes and functions reduce to ``__name__`` so no memory address can
    leak into the snapshot.
    """
    if value is None:
        return None
    if isinstance(value, str):
        # A postponed (string) annotation is already its own stable text.
        return value
    if isinstance(value, type):
        return value.__name__
    if get_origin(value) is not None or isinstance(value, (GenericAlias, UnionType)):
        return str(value)
    return getattr(value, "__name__", None) or getattr(value, "__qualname__", None) or type(value).__name__


def _enum_members(target: type) -> dict[str, Any]:
    return {member.name: member.value for member in target}


def _model_fields(target: type) -> dict[str, Any]:
    """Field-level contract for Pydantic models and dataclasses."""
    if is_dataclass(target):
        return {
            field.name: {
                "annotation": _annotation_text(field.type),
                "required": field.default is MISSING and field.default_factory is MISSING,
            }
            for field in dataclass_fields(target)
        }
    fields = getattr(target, "model_fields", None)
    if not isinstance(fields, dict):
        return {}
    described = {}
    for name, info in fields.items():
        described[name] = {
            "annotation": _annotation_text(getattr(info, "annotation", None)),
            "required": bool(info.is_required()) if callable(getattr(info, "is_required", None)) else None,
        }
    return described


def _own_methods(target: type) -> dict[str, str | None]:
    methods = {}
    for name, value in vars(target).items():
        if name.startswith("_") and name != "__init__":
            continue
        if not callable(value):
            continue
        methods[name] = _signature(value)
    return methods


def _describe(qualified_name: str, target: Any) -> dict[str, Any]:
    if inspect.isclass(target):
        described: dict[str, Any] = {
            "kind": "class",
            "bases": [f"{base.__module__}.{base.__qualname__}" for base in target.__bases__],
            "init": _signature(target.__init__) if "__init__" in vars(target) else None,
            "methods": _own_methods(target),
        }
        if issubclass(target, Enum):
            described["members"] = _enum_members(target)
        fields = _model_fields(target)
        if fields:
            described["fields"] = fields
        return described
    if callable(target):
        return {"kind": "callable", "signature": _signature(target)}
    if isinstance(target, dict):
        return {"kind": "mapping", "value": {key: target[key] for key in sorted(target)}}
    if isinstance(target, (frozenset, set)):
        # Sorted: set repr order depends on PYTHONHASHSEED and would make the
        # snapshot differ between runs.
        return {"kind": "set", "value": sorted(str(item) for item in target)}
    if isinstance(target, (list, tuple)):
        return {"kind": "sequence", "value": [repr(item) for item in target]}
    return {"kind": type(target).__name__, "value": repr(target)}


def _cli_flags(parser: argparse.ArgumentParser | None = None) -> list[dict[str, Any]]:
    if parser is None:
        parser = importlib.import_module("cela_sh.cli").build_parser()
    flags = []
    for action in parser._actions:  # noqa: SLF001 - argparse exposes no public accessor
        flags.append(
            {
                "options": sorted(action.option_strings),
                "required": bool(action.required),
                "default": repr(action.default),
                "type": _annotation_text(action.type) if action.type is not None else None,
                "nargs": repr(action.nargs) if action.nargs is not None else None,
            }
        )
    return flags


def _python_surface() -> dict[str, Any]:
    cli = importlib.import_module("cela_sh.cli")
    learning_cli = importlib.import_module("cela_sh.learning_cli")
    registry = importlib.import_module("cela_sh.memory.registry")
    planner_registry = importlib.import_module("cela_sh.planner.registry")
    exports: dict[str, Any] = {}
    package_exports: dict[str, list[str]] = {}

    for package_name in PYTHON_PACKAGES:
        package = importlib.import_module(package_name)
        names = sorted(getattr(package, "__all__", []))
        package_exports[package_name] = names
        if package_name != "cela_sh":
            continue
        for name in names:
            exports[name] = _describe(f"{package_name}.{name}", getattr(package, name))

    return {
        "exports": exports,
        "package_exports": package_exports,
        "cli_flags": _cli_flags(),
        "cli_subcommands": {
            "learn": _cli_flags(learning_cli.build_parser()),
            "learn-file": _cli_flags(learning_cli.build_file_parser()),
            "search": _cli_flags(learning_cli.build_search_parser()),
            "brief": _cli_flags(learning_cli.build_brief_parser()),
        },
        "exit_codes": dict(sorted(cli.EXIT_CODES.items())),
        "memory_backends": list(registry.memory_backend_names()),
        "builtin_memory_backends": sorted(registry.BUILTIN_MEMORY_BACKENDS),
        "planner_providers": list(planner_registry.planner_provider_names()),
        "builtin_planner_providers": sorted(planner_registry.BUILTIN_PLANNER_PROVIDERS),
    }


def _worker_surface() -> dict[str, Any]:
    source = WORKER_SOURCE.read_text(encoding="utf-8")
    routes = [{"path": path, "method": method} for path, method in WORKER_ROUTE.findall(source)]
    routes.sort(key=lambda route: (route["path"], route["method"]))
    return {
        "source": str(WORKER_SOURCE.relative_to(ROOT)),
        "exports": sorted(set(WORKER_FUNCTION.findall(source)) | set(WORKER_CLASS.findall(source))),
        "has_default_export": bool(WORKER_DEFAULT.search(source)),
        "routes": routes,
        "route_prefixes": sorted(set(WORKER_ROUTE_PREFIX.findall(source))),
    }


def build_surface() -> dict[str, Any]:
    """Compute the live contract surface of the whole repository."""
    return {
        "schema_version": SCHEMA_VERSION,
        "python": _python_surface(),
        "worker": _worker_surface(),
    }


def render(surface: dict[str, Any]) -> str:
    return json.dumps(surface, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def load_snapshot() -> dict[str, Any] | None:
    if not SNAPSHOT_PATH.is_file():
        return None
    return json.loads(SNAPSHOT_PATH.read_text(encoding="utf-8"))


def summarise_drift(recorded: dict[str, Any], current: dict[str, Any]) -> list[str]:
    """Human-readable list of what a reviewer has to approve."""
    notes: list[str] = []
    recorded_exports = recorded.get("python", {}).get("exports", {})
    current_exports = current.get("python", {}).get("exports", {})
    for name in sorted(set(recorded_exports) - set(current_exports)):
        notes.append(f"REMOVED public name: {name}")
    for name in sorted(set(current_exports) - set(recorded_exports)):
        notes.append(f"ADDED public name: {name}")
    for name in sorted(set(recorded_exports) & set(current_exports)):
        if recorded_exports[name] != current_exports[name]:
            notes.append(f"CHANGED public name: {name}")

    recorded_python = recorded.get("python", {})
    current_python = current.get("python", {})
    recorded_modes = {"default": recorded_python.get("cli_flags", [])}
    current_modes = {"default": current_python.get("cli_flags", [])}
    recorded_modes.update(recorded_python.get("cli_subcommands", {}))
    current_modes.update(current_python.get("cli_subcommands", {}))
    for mode in sorted(set(recorded_modes) - set(current_modes)):
        notes.append(f"REMOVED CLI subcommand: {mode}")
    for mode in sorted(set(current_modes) - set(recorded_modes)):
        notes.append(f"ADDED CLI subcommand: {mode}")
    for mode in sorted(set(recorded_modes) & set(current_modes)):
        recorded_flags = {tuple(flag["options"]) for flag in recorded_modes[mode]}
        current_flags = {tuple(flag["options"]) for flag in current_modes[mode]}
        label = "CLI flag" if mode == "default" else f"CLI {mode} flag"
        for flag in sorted(recorded_flags - current_flags):
            notes.append(f"REMOVED {label}: {'/'.join(flag)}")
        for flag in sorted(current_flags - recorded_flags):
            notes.append(f"ADDED {label}: {'/'.join(flag)}")

    for key, label in (
        ("exit_codes", "exit code"),
        ("memory_backends", "memory backend"),
        ("planner_providers", "planner provider"),
    ):
        before = recorded.get("python", {}).get(key, {})
        after = current.get("python", {}).get(key, {})
        if isinstance(before, dict):
            for name in sorted(set(before) - set(after)):
                notes.append(f"REMOVED {label}: {name}")
            for name in sorted(set(after)):
                if name in before and before[name] != after[name]:
                    notes.append(f"CHANGED {label}: {name} {before[name]} -> {after[name]}")
        else:
            for name in sorted(set(before) - set(after)):
                notes.append(f"REMOVED {label}: {name}")

    recorded_routes = {
        (route["method"], route["path"]) for route in recorded.get("worker", {}).get("routes", [])
    }
    current_routes = {(route["method"], route["path"]) for route in current.get("worker", {}).get("routes", [])}
    for method, path in sorted(recorded_routes - current_routes):
        notes.append(f"REMOVED Worker route: {method} {path}")
    for method, path in sorted(current_routes - recorded_routes):
        notes.append(f"ADDED Worker route: {method} {path}")

    return notes


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--check", action="store_true", help="fail when the live surface differs from the snapshot")
    mode.add_argument("--update", action="store_true", help="rewrite the snapshot after an intentional change")
    args = parser.parse_args(argv)

    current = build_surface()

    if args.update:
        SNAPSHOT_PATH.parent.mkdir(parents=True, exist_ok=True)
        SNAPSHOT_PATH.write_text(render(current), encoding="utf-8")
        sys.stderr.write(f"Updated {SNAPSHOT_PATH.relative_to(ROOT)}.\n")
        return 0

    if not args.check:
        sys.stdout.write(render(current))
        return 0

    recorded = load_snapshot()
    if recorded is None:
        sys.stderr.write(
            f"Missing {SNAPSHOT_PATH.relative_to(ROOT)}. Create it with: python tools/api_surface.py --update\n"
        )
        return 1

    if recorded == current:
        sys.stderr.write("Public contract surface matches the recorded snapshot.\n")
        return 0

    notes = summarise_drift(recorded, current)
    sys.stderr.write("Public contract surface drifted from the recorded snapshot.\n")
    for note in notes:
        sys.stderr.write(f"  - {note}\n")
    sys.stderr.write(
        "\nReview every line above. If the change is intentional and backwards compatible,\n"
        "record it with: bash ./cela.sh api-surface --update\n"
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
