"""Local-first CLI for explicitly learning, importing, and searching RAG memory."""

import argparse
import json
import math
import sys
from collections.abc import Sequence
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from .cli import EXIT_CODES, JsonArgumentParser, write_error
from .factory import create_planner
from .memory import DISABLED_BACKEND, MemoryStoreError
from .memory.document_loader import DocumentLoadError, _safe_source_path, chunk_text, extract_document
from .memory.metadata import (
    DECISION_STATUSES,
    MEMORY_TYPES,
    correction_first,
    metadata_matches,
    normalize_decision_status,
    normalize_memory_type,
    normalize_tags,
)
from .memory.scopes import (
    MEMORY_SCOPES,
    SEARCH_SCOPES,
    memory_session_id,
    search_session_ids,
)
from .memory.sources import content_hash
from .planner.models import Plan
from .settings import ConfigurationError, Settings


def _validate_content(value: str) -> str:
    if not value.strip():
        raise argparse.ArgumentTypeError("must not be blank")
    if len(value) > 8_000:
        raise argparse.ArgumentTypeError("must contain no more than 8000 characters")
    return value


def _content(value: str) -> str:
    return value if value == "-" else _validate_content(value)


def _session_id(value: str) -> str:
    if not value.strip():
        raise argparse.ArgumentTypeError("must not be blank")
    if len(value) > 256:
        raise argparse.ArgumentTypeError("must contain no more than 256 characters")
    return value.strip()


def _tag(value: str) -> str:
    try:
        return normalize_tags([value])[0]
    except ValueError as exc:
        raise argparse.ArgumentTypeError(str(exc)) from exc


def _query(value: str) -> str:
    if not value.strip():
        raise argparse.ArgumentTypeError("must not be blank")
    if len(value) > 8_000:
        raise argparse.ArgumentTypeError("must contain no more than 8000 characters")
    return value


def _limit(value: str) -> int:
    try:
        number = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("must be an integer between 1 and 100") from exc
    if number < 1 or number > 100:
        raise argparse.ArgumentTypeError("must be an integer between 1 and 100")
    return number


def _plan_path(value: str) -> str:
    if not value.strip():
        raise argparse.ArgumentTypeError("must not be blank")
    return value.strip()


def _add_write_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--scope", choices=MEMORY_SCOPES, default="project", help="Memory visibility scope")
    parser.add_argument("--session-id", type=_session_id, help="Required for project scope; omit for global scope")
    parser.add_argument(
        "--type",
        dest="memory_type",
        choices=MEMORY_TYPES,
        default=argparse.SUPPRESS,
        help="Knowledge type (default: note); corrections receive retrieval priority",
    )
    parser.add_argument("--tag", action="append", default=[], type=_tag, help="A repeatable search tag")


def build_parser() -> argparse.ArgumentParser:
    """Build the original ``learn`` parser for API-surface compatibility."""
    parser = JsonArgumentParser(
        prog="cela.sh learn",
        description="Store explicitly approved knowledge in the configured vector memory.",
    )
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument(
        "--content",
        type=_content,
        help="A note, correction, or fact; use '-' to read up to 8000 characters from stdin",
    )
    source.add_argument(
        "--from-plan",
        type=_plan_path,
        help=(
            "Capture one approved CELA plan JSON file as an active decision "
            "(replace-by-source on the plan file)"
        ),
    )
    _add_write_options(parser)
    parser.add_argument(
        "--reason",
        type=_validate_content,
        help="Decision rationale; required with --type decision",
    )
    parser.add_argument(
        "--rejected",
        action="append",
        default=[],
        type=_validate_content,
        help="A rejected alternative for a decision; repeatable, at most 10",
    )
    parser.add_argument(
        "--status",
        choices=DECISION_STATUSES,
        default="active",
        help="Decision lifecycle status; decision type only",
    )
    parser.add_argument(
        "--superseded-by",
        type=_validate_content,
        help="What replaced this decision; required when --status superseded",
    )
    parser.add_argument("--why", type=_validate_content, help="Failure cause; failure type only")
    return parser


def build_brief_parser() -> argparse.ArgumentParser:
    parser = JsonArgumentParser(
        prog="cela.sh brief",
        description=(
            "Rebuild the project brief from stored memory: goal, decisions, failures, "
            "technical debt, and next steps. No model or embedding is required."
        ),
    )
    parser.add_argument(
        "--session-id",
        type=_session_id,
        required=True,
        help="Project session whose memory is summarized",
    )
    parser.add_argument(
        "--limit",
        type=_limit,
        default=100,
        help="Maximum records considered per section, from 1 to 100",
    )
    parser.add_argument(
        "--text",
        action="store_true",
        help="Print a human-readable brief instead of JSON",
    )
    return parser


def build_file_parser() -> argparse.ArgumentParser:
    parser = JsonArgumentParser(
        prog="cela.sh learn-file",
        description="Extract and chunk one local Markdown, text, or PDF file into RAG memory.",
    )
    parser.add_argument("path", help="Local .md, .txt, or .pdf file")
    _add_write_options(parser)
    return parser


def build_search_parser() -> argparse.ArgumentParser:
    parser = JsonArgumentParser(
        prog="cela.sh search",
        description="Search project/global RAG memory semantically or by metadata tags.",
    )
    parser.add_argument("--query", type=_query, help="Optional semantic query; omit for metadata browsing")
    parser.add_argument("--scope", choices=SEARCH_SCOPES, default="project", help="Search project, global, or both")
    parser.add_argument("--session-id", type=_session_id, help="Required for project/both scope")
    parser.add_argument("--type", dest="memory_type", choices=MEMORY_TYPES, help="Filter by knowledge type")
    parser.add_argument("--tag", action="append", default=[], type=_tag, help="Filter by all supplied tags")
    parser.add_argument("--limit", type=_limit, default=20, help="Maximum results, from 1 to 100")
    return parser


def _scoped_session(parser: argparse.ArgumentParser, scope: str, session_id: str | None) -> str:
    try:
        return memory_session_id(scope, session_id)
    except ValueError as exc:
        parser.error(str(exc))
    raise AssertionError("parser.error must exit")  # pragma: no cover


def _create_store() -> tuple[Settings, Any]:
    settings = Settings.from_env()
    if settings.memory_backend == DISABLED_BACKEND:
        raise ConfigurationError("Enable memory with CELA_MEMORY_BACKEND=sqlite-vec or qdrant.")
    planner = create_planner(settings)
    store = planner.memory_store
    if store is None:
        raise ConfigurationError("The configured planner did not provide an enabled memory backend.")
    return settings, store


def _write_metadata(scope: str, memory_type: str, tags: list[str], *, source: str) -> dict[str, Any]:
    return {
        "scope": scope,
        "type": memory_type,
        "tags": normalize_tags(tags),
        "source": source,
        "recorded_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }


def _decision_fields(parser: argparse.ArgumentParser, args: argparse.Namespace, memory_type: str) -> dict[str, Any]:
    """Validate the decision/failure options for the chosen type and build metadata extras."""
    if args.reason is not None and memory_type != "decision":
        parser.error("--reason is only valid with --type decision")
    if args.rejected and memory_type != "decision":
        parser.error("--rejected is only valid with --type decision")
    if args.status != "active" and memory_type != "decision":
        parser.error("--status is only valid with --type decision")
    if args.superseded_by is not None and memory_type != "decision":
        parser.error("--superseded-by is only valid with --type decision")
    if args.why is not None and memory_type != "failure":
        parser.error("--why is only valid with --type failure")

    extra: dict[str, Any] = {}
    if memory_type == "decision":
        if args.reason is None:
            parser.error("--reason is required with --type decision")
        if len(args.rejected) > 10:
            parser.error("at most 10 rejected alternatives are allowed")
        if args.status == "superseded" and not args.superseded_by:
            parser.error("--superseded-by is required when --status superseded")
        if args.superseded_by is not None and args.status != "superseded":
            parser.error("--superseded-by is only valid with --status superseded")
        extra["rationale"] = args.reason
        extra["rejected_alternatives"] = list(args.rejected)
        extra["status"] = args.status
        if args.status == "superseded":
            extra["superseded_by"] = args.superseded_by
    if memory_type == "failure" and args.why is not None:
        extra["why"] = args.why
    return extra


def _learn(args: argparse.Namespace, parser: argparse.ArgumentParser) -> int:
    if args.from_plan is not None:
        return _learn_from_plan(args, parser)
    session = _scoped_session(parser, args.scope, args.session_id)
    memory_type = normalize_memory_type(getattr(args, "memory_type", None))
    decision_extra = _decision_fields(parser, args, memory_type)
    content = args.content
    if content == "-":
        try:
            content = _validate_content(sys.stdin.read(8_001))
        except argparse.ArgumentTypeError as exc:
            write_error("cli_usage_error", str(exc))
            return EXIT_CODES["cli_usage_error"]
        except KeyboardInterrupt:
            write_error("interrupted", "Learning was interrupted.")
            return EXIT_CODES["interrupted"]

    try:
        settings, store = _create_store()
        metadata = _write_metadata(
            args.scope,
            memory_type,
            args.tag,
            source="explicit_cli_learning",
        )
        metadata.update(decision_extra)
        stored = store.add_memory(session, content, metadata)
        if stored is not True:
            raise MemoryStoreError("Memory store did not confirm the write.")
    except (ConfigurationError, ValueError) as exc:
        write_error("configuration_error", str(exc))
        return EXIT_CODES["configuration_error"]
    except MemoryStoreError as exc:
        write_error("planner_memory_error", str(exc))
        return EXIT_CODES["planner_memory_error"]
    except KeyboardInterrupt:
        write_error("interrupted", "Learning was interrupted.")
        return EXIT_CODES["interrupted"]

    output = {
        "learned": True,
        "scope": args.scope,
        "type": memory_type,
        "tags": metadata["tags"],
        "memory_backend": settings.memory_backend,
    }
    if memory_type == "decision":
        output["status"] = metadata["status"]
    if args.scope == "project":
        output["session_id"] = args.session_id
    sys.stdout.write(json.dumps(output, ensure_ascii=False) + "\n")
    return EXIT_CODES["ok"]


def _learn_from_plan(args: argparse.Namespace, parser: argparse.ArgumentParser) -> int:
    """Capture an approved plan JSON file as an active decision via replace-by-source."""
    explicit_type = getattr(args, "memory_type", None)
    if explicit_type is not None and explicit_type != "decision":
        parser.error("--from-plan captures an approved plan as a decision; do not pass --type")
    if args.reason is not None:
        parser.error("--from-plan derives the rationale from the plan file; do not pass --reason")
    if args.status != "active":
        parser.error("--from-plan stores an active decision; record superseded decisions separately")
    if args.superseded_by is not None:
        parser.error("--superseded-by is not valid with --from-plan")
    if args.why is not None:
        parser.error("--why is not valid with --from-plan")

    session = _scoped_session(parser, args.scope, args.session_id)
    plan_path = Path(args.from_plan).expanduser()
    try:
        raw = plan_path.read_bytes()
    except OSError as exc:
        write_error("configuration_error", f"Cannot read plan file {args.from_plan!r}: {exc.strerror or exc}")
        return EXIT_CODES["configuration_error"]
    if len(raw) > 512_000:
        write_error("configuration_error", "Plan files must not exceed 512000 bytes.")
        return EXIT_CODES["configuration_error"]
    try:
        plan = Plan.model_validate_json(raw)
    except ValidationError as exc:
        write_error(
            "configuration_error",
            f"Plan file is not a valid CELA plan JSON ({exc.error_count()} validation error(s)).",
        )
        return EXIT_CODES["configuration_error"]

    content = plan.analysis
    source_name = plan_path.name
    try:
        source_path = _safe_source_path(plan_path)
    except (ValueError, DocumentLoadError) as exc:
        write_error("configuration_error", str(exc))
        return EXIT_CODES["configuration_error"]
    source_hash = sha256(raw).hexdigest()
    imported_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    rationale = f"Approved plan from {source_name} ({len(plan.steps)} steps; plan status {plan.status.value})"
    base_metadata = _write_metadata(args.scope, "decision", args.tag, source="explicit_cli_plan_learning")
    metadata = {
        **base_metadata,
        "source_name": source_name,
        "source_path": source_path,
        "source_hash": source_hash,
        "source_sha256": source_hash,
        "content_hash": content_hash(content),
        "imported_at": imported_at,
        "chunk_index": 1,
        "chunk_count": 1,
        "rationale": rationale,
        "rejected_alternatives": list(args.rejected),
        "status": "active",
    }

    try:
        settings, store = _create_store()
        result = store.replace_source(session, source_path, source_hash, [(content, metadata)])
        if not isinstance(result, dict) or result.get("status") not in {"imported", "updated", "unchanged"}:
            raise MemoryStoreError("Memory store returned an invalid source-import result.")
        stored_chunks = result.get("chunks_stored")
        replaced_chunks = result.get("replaced_chunks")
        existing_chunks = result.get("existing_chunks")
        if any(
            isinstance(value, bool) or not isinstance(value, int) or value < 0
            for value in (stored_chunks, replaced_chunks, existing_chunks)
        ):
            raise MemoryStoreError("Memory store returned invalid source-import counts.")
    except (ConfigurationError, ValueError) as exc:
        write_error("configuration_error", str(exc))
        return EXIT_CODES["configuration_error"]
    except MemoryStoreError as exc:
        write_error("planner_memory_error", str(exc))
        return EXIT_CODES["planner_memory_error"]
    except KeyboardInterrupt:
        write_error("interrupted", "Plan capture was interrupted; retry the same file to reconcile it.")
        return EXIT_CODES["interrupted"]

    status = result["status"]
    if status == "unchanged":
        message = "No changes detected; the approved-plan decision was retained."
    elif status == "updated":
        message = f"Updated approved plan {source_path}; replaced {replaced_chunks} chunk(s) with {stored_chunks}."
    else:
        message = f"Captured approved plan {source_path} as an active decision."

    output = {
        "learned": status != "unchanged",
        "metadata_applied": status != "unchanged",
        "status": status,
        "message": message,
        "scope": args.scope,
        "type": "decision",
        "decision_status": "active",
        "tags": base_metadata["tags"],
        "source": source_name,
        "source_path": source_path,
        "source_hash": source_hash,
        "steps": len(plan.steps),
        "chunks_stored": stored_chunks,
        "replaced_chunks": replaced_chunks,
        "existing_chunks": existing_chunks,
        "memory_backend": settings.memory_backend,
    }
    if args.scope == "project":
        output["session_id"] = args.session_id
    sys.stdout.write(json.dumps(output, ensure_ascii=False) + "\n")
    return EXIT_CODES["ok"]


def _learn_file(args: argparse.Namespace, parser: argparse.ArgumentParser) -> int:
    session = _scoped_session(parser, args.scope, args.session_id)
    try:
        settings, store = _create_store()
        document = extract_document(args.path)
        chunks = chunk_text(document.text)
        file_type = normalize_memory_type(getattr(args, "memory_type", None))
        base_metadata = _write_metadata(
            args.scope,
            file_type,
            args.tag,
            source="explicit_cli_file_learning",
        )
        imported_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
        records = []
        for index, chunk in enumerate(chunks, start=1):
            chunk_metadata = {
                **base_metadata,
                "source_name": document.source_name,
                "source_path": document.source_path,
                "source_hash": document.content_sha256,
                "source_sha256": document.content_sha256,
                "content_hash": content_hash(chunk),
                "imported_at": imported_at,
                "chunk_index": index,
                "chunk_count": len(chunks),
            }
            records.append((chunk, chunk_metadata))
    except (ConfigurationError, DocumentLoadError, ValueError) as exc:
        write_error("configuration_error", str(exc))
        return EXIT_CODES["configuration_error"]
    except MemoryStoreError as exc:
        write_error("planner_memory_error", str(exc))
        return EXIT_CODES["planner_memory_error"]
    except KeyboardInterrupt:
        write_error("interrupted", "Learning was interrupted.")
        return EXIT_CODES["interrupted"]

    try:
        result = store.replace_source(
            session,
            document.source_path,
            document.content_sha256,
            records,
        )
        if not isinstance(result, dict) or result.get("status") not in {"imported", "updated", "unchanged"}:
            raise MemoryStoreError("Memory store returned an invalid source-import result.")
        stored_chunks = result.get("chunks_stored")
        replaced_chunks = result.get("replaced_chunks")
        existing_chunks = result.get("existing_chunks")
        if any(
            isinstance(value, bool) or not isinstance(value, int) or value < 0
            for value in (stored_chunks, replaced_chunks, existing_chunks)
        ):
            raise MemoryStoreError("Memory store returned invalid source-import counts.")
    except ValueError as exc:
        write_error("configuration_error", str(exc))
        return EXIT_CODES["configuration_error"]
    except MemoryStoreError as exc:
        write_error("planner_memory_error", str(exc))
        return EXIT_CODES["planner_memory_error"]
    except KeyboardInterrupt:
        write_error("interrupted", "File import was interrupted; retry the same source to reconcile it.")
        return EXIT_CODES["interrupted"]

    status = result["status"]
    if status == "unchanged":
        message = "No changes detected; existing tags/type were retained."
    elif status == "updated":
        message = (
            f"Updated source {document.source_path}; replaced {replaced_chunks} chunks "
            f"with {stored_chunks} chunks."
        )
    else:
        message = f"Imported {stored_chunks} chunks."

    output = {
        "learned": status != "unchanged",
        "metadata_applied": status != "unchanged",
        "status": status,
        "message": message,
        "scope": args.scope,
        "type": file_type,
        "tags": base_metadata["tags"],
        "source": document.source_name,
        "source_path": document.source_path,
        "source_hash": document.content_sha256,
        "chunks_stored": stored_chunks,
        "replaced_chunks": replaced_chunks,
        "existing_chunks": existing_chunks,
        "memory_backend": settings.memory_backend,
    }
    if args.scope == "project":
        output["session_id"] = args.session_id
    sys.stdout.write(json.dumps(output, ensure_ascii=False) + "\n")
    return EXIT_CODES["ok"]


def _memory_records(
    store: Any,
    query: str | None,
    session: str,
    limit: int,
    tags: list[str],
    memory_type: str | None,
) -> list[dict[str, Any]]:
    if query is None:
        return store.list_memory(session, limit=limit, tags=tags, memory_type=memory_type)
    candidates = store.search_memory(query, session, limit=min(max(limit * 5, limit), 1_000))
    return [
        memory
        for memory in candidates
        if isinstance(memory, dict)
        and metadata_matches(memory.get("metadata"), tags=tags, memory_type=memory_type)
    ]


def _search(args: argparse.Namespace, parser: argparse.ArgumentParser) -> int:
    try:
        scopes = search_session_ids(args.scope, args.session_id)
    except ValueError as exc:
        parser.error(str(exc))
    try:
        tags = normalize_tags(args.tag)
        _, store = _create_store()
        matches = []
        for scope, session in scopes:
            memories = _memory_records(
                store,
                args.query,
                session,
                args.limit,
                tags,
                args.memory_type,
            )
            for memory in memories:
                metadata = memory.get("metadata") if isinstance(memory, dict) else None
                content = memory.get("content") if isinstance(memory, dict) else None
                score = memory.get("score") if isinstance(memory, dict) else None
                if (
                    not isinstance(metadata, dict)
                    or memory.get("session_id") != session
                    or not isinstance(content, str)
                    or not content.strip()
                    or len(content) > 8_000
                    or isinstance(score, bool)
                    or not isinstance(score, (int, float))
                    or not math.isfinite(score)
                ):
                    raise MemoryStoreError("Memory store returned invalid results.")
                try:
                    record_type = normalize_memory_type(metadata.get("type"))
                    record_tags = normalize_tags(metadata.get("tags", []))
                except ValueError:
                    record_type, record_tags = "note", []
                matches.append(
                    {
                        "content": content,
                        "scope": scope,
                        "type": record_type,
                        "tags": record_tags,
                        "metadata": metadata,
                        "score": float(score),
                    }
                )
        matches = correction_first(matches)[: args.limit]
    except (ConfigurationError, ValueError) as exc:
        write_error("configuration_error", str(exc))
        return EXIT_CODES["configuration_error"]
    except MemoryStoreError as exc:
        write_error("planner_memory_error", str(exc))
        return EXIT_CODES["planner_memory_error"]
    except KeyboardInterrupt:
        write_error("interrupted", "Memory search was interrupted.")
        return EXIT_CODES["interrupted"]

    sys.stdout.write(
        json.dumps(
            {"scope": args.scope, "count": len(matches), "results": matches},
            ensure_ascii=False,
        )
        + "\n"
    )
    return EXIT_CODES["ok"]


def _brief_records(raw: list[Any], session: str) -> list[dict[str, Any]]:
    """Validate raw list_memory results before any brief assembly trusts them."""
    records = []
    for memory in raw:
        metadata = memory.get("metadata") if isinstance(memory, dict) else None
        content = memory.get("content") if isinstance(memory, dict) else None
        score = memory.get("score") if isinstance(memory, dict) else None
        if (
            not isinstance(metadata, dict)
            or memory.get("session_id") != session
            or not isinstance(content, str)
            or not content.strip()
            or len(content) > 8_000
            or isinstance(score, bool)
            or not isinstance(score, (int, float))
            or not math.isfinite(score)
        ):
            raise MemoryStoreError("Memory store returned invalid results.")
        records.append({"content": content, "metadata": metadata, "score": float(score)})
    return records


def _render_brief(brief: dict[str, Any]) -> str:
    lines = [
        f"PROJECT BRIEF — {brief['session_id']} (generated {brief['generated_at']})",
        f"Backend: {brief['memory_backend']}",
        "",
        "GOAL",
    ]
    lines.append(f"  {brief['project_goal']}" if brief["project_goal"] else "  (none recorded)")
    lines += ["", f"ACTIVE DECISIONS ({brief['counts']['active_decisions']})"]
    if brief["active_decisions"]:
        for decision in brief["active_decisions"]:
            lines.append(f"  - {decision['content']}")
            if decision.get("rationale"):
                lines.append(f"      why: {decision['rationale']}")
            if decision.get("rejected_alternatives"):
                lines.append("      rejected: " + "; ".join(decision["rejected_alternatives"]))
            if decision.get("recorded_at"):
                lines.append(f"      recorded: {decision['recorded_at']}")
    else:
        lines.append("  (none)")
    lines += ["", f"CLOSED DECISIONS ({brief['counts']['closed_decisions']})"]
    if brief["closed_decisions"]:
        for decision in brief["closed_decisions"]:
            note = f" (superseded by: {decision['superseded_by']})" if decision.get("superseded_by") else ""
            lines.append(f"  - [{decision['status']}] {decision['content']}{note}")
    else:
        lines.append("  (none)")
    lines += ["", f"PAST FAILURES ({brief['counts']['failures']})"]
    if brief["failures"]:
        for failure in brief["failures"]:
            lines.append(f"  - {failure['content']}")
            if failure.get("why"):
                lines.append(f"      cause: {failure['why']}")
    else:
        lines.append("  (none)")
    lines += ["", f"TECHNICAL DEBT ({brief['counts']['technical_debt']})"]
    lines += [f"  - {item['content']}" for item in brief["technical_debt"]] or ["  (none)"]
    lines += ["", f"NEXT STEPS ({brief['counts']['next_steps']})"]
    lines += [f"  - {item['content']}" for item in brief["next_steps"]] or ["  (none)"]
    oldest = brief["memory_range"]["oldest_recorded_at"]
    newest = brief["memory_range"]["newest_recorded_at"]
    if oldest or newest:
        lines += ["", f"MEMORY RANGE: {oldest or '?'} .. {newest or '?'}"]
    if not brief["has_context"]:
        lines += [
            "",
            "No project memory yet. Record the first decision:",
            '  bash ./cela.sh learn --session-id <id> --type decision --content "..." --reason "..."',
        ]
    return "\n".join(lines)


def _brief(args: argparse.Namespace, parser: argparse.ArgumentParser) -> int:
    try:
        session = memory_session_id("project", args.session_id)
    except ValueError as exc:
        parser.error(str(exc))
    try:
        settings, store = _create_store()
    except (ConfigurationError, ValueError) as exc:
        write_error("configuration_error", str(exc))
        return EXIT_CODES["configuration_error"]
    except MemoryStoreError as exc:
        write_error("planner_memory_error", str(exc))
        return EXIT_CODES["planner_memory_error"]
    except KeyboardInterrupt:
        write_error("interrupted", "Briefing was interrupted.")
        return EXIT_CODES["interrupted"]

    try:
        goal_raw = store.list_memory(session, limit=args.limit, tags=["goal"])
        decision_raw = store.list_memory(session, limit=args.limit, memory_type="decision")
        failure_raw = store.list_memory(session, limit=args.limit, memory_type="failure")
        debt_raw = store.list_memory(session, limit=args.limit, memory_type="debt")
        next_raw = store.list_memory(session, limit=args.limit, tags=["next"])
        goals = _brief_records(goal_raw, session)
        decisions = _brief_records(decision_raw, session)
        failures = _brief_records(failure_raw, session)
        debts = _brief_records(debt_raw, session)
        nexts = _brief_records(next_raw, session)
    except MemoryStoreError as exc:
        write_error("planner_memory_error", str(exc))
        return EXIT_CODES["planner_memory_error"]
    except ValueError as exc:
        write_error("configuration_error", str(exc))
        return EXIT_CODES["configuration_error"]
    except KeyboardInterrupt:
        write_error("interrupted", "Briefing was interrupted.")
        return EXIT_CODES["interrupted"]

    active_decisions: list[dict[str, Any]] = []
    closed_decisions: list[dict[str, Any]] = []
    for record in decisions:
        metadata = record["metadata"]
        try:
            status = normalize_decision_status(metadata.get("status"))
        except ValueError:
            write_error("planner_memory_error", "A stored decision has an invalid status.")
            return EXIT_CODES["planner_memory_error"]
        alternatives = metadata.get("rejected_alternatives", [])
        if not isinstance(alternatives, list) or not all(isinstance(item, str) for item in alternatives):
            write_error("planner_memory_error", "A stored decision has invalid rejected alternatives.")
            return EXIT_CODES["planner_memory_error"]
        if status == "active":
            active_decisions.append(
                {
                    "content": record["content"],
                    "rationale": metadata.get("rationale"),
                    "rejected_alternatives": alternatives,
                    "recorded_at": metadata.get("recorded_at"),
                    "source": metadata.get("source"),
                }
            )
        else:
            closed_decisions.append(
                {
                    "content": record["content"],
                    "status": status,
                    "superseded_by": metadata.get("superseded_by"),
                    "recorded_at": metadata.get("recorded_at"),
                }
            )

    failure_entries = [
        {"content": record["content"], "why": record["metadata"].get("why"), "recorded_at": record["metadata"].get("recorded_at")}
        for record in failures
    ]
    debt_entries = [
        {"content": record["content"], "recorded_at": record["metadata"].get("recorded_at")} for record in debts
    ]
    next_entries = [
        {"content": record["content"], "recorded_at": record["metadata"].get("recorded_at")} for record in nexts
    ]
    project_goal = goals[0]["content"] if goals else None
    timestamps = [
        record["metadata"].get("recorded_at")
        for record in goals + decisions + failures + debts + nexts
        if isinstance(record["metadata"].get("recorded_at"), str) and record["metadata"].get("recorded_at")
    ]

    brief = {
        "scope": "project",
        "session_id": args.session_id,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "has_context": bool(
            project_goal or active_decisions or closed_decisions or failure_entries or debt_entries or next_entries
        ),
        "project_goal": project_goal,
        "active_decisions": active_decisions,
        "closed_decisions": closed_decisions,
        "failures": failure_entries,
        "technical_debt": debt_entries,
        "next_steps": next_entries,
        "counts": {
            "active_decisions": len(active_decisions),
            "closed_decisions": len(closed_decisions),
            "failures": len(failure_entries),
            "technical_debt": len(debt_entries),
            "next_steps": len(next_entries),
        },
        "memory_range": {
            "oldest_recorded_at": min(timestamps) if timestamps else None,
            "newest_recorded_at": max(timestamps) if timestamps else None,
        },
        "memory_backend": settings.memory_backend,
    }
    if args.text:
        sys.stdout.write(_render_brief(brief) + "\n")
    else:
        sys.stdout.write(json.dumps(brief, ensure_ascii=False, indent=2) + "\n")
    return EXIT_CODES["ok"]


_COMMANDS = {
    "learn": (build_parser, _learn),
    "learn-file": (build_file_parser, _learn_file),
    "search": (build_search_parser, _search),
    "brief": (build_brief_parser, _brief),
}


def main(argv: Sequence[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    if not arguments or arguments[0] not in _COMMANDS:
        parser = JsonArgumentParser(prog="cela.sh", description="Local RAG memory commands")
        parser.error(f"expected one of: {', '.join(_COMMANDS)}")
    command = arguments[0]
    parser_builder, handler = _COMMANDS[command]
    parser = parser_builder()
    args = parser.parse_args(arguments[1:])
    return handler(args, parser)


if __name__ == "__main__":
    raise SystemExit(main())
