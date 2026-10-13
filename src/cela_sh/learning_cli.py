"""Local-first CLI for explicitly learning, importing, and searching RAG memory."""

import argparse
import json
import math
import sys
from collections.abc import Sequence
from datetime import datetime, timezone
from typing import Any

from .cli import EXIT_CODES, JsonArgumentParser, write_error
from .factory import create_planner
from .memory import DISABLED_BACKEND, MemoryStoreError
from .memory.document_loader import DocumentLoadError, chunk_text, extract_document
from .memory.metadata import (
    MEMORY_TYPES,
    correction_first,
    metadata_matches,
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


def _add_write_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--scope", choices=MEMORY_SCOPES, default="project", help="Memory visibility scope")
    parser.add_argument("--session-id", type=_session_id, help="Required for project scope; omit for global scope")
    parser.add_argument(
        "--type",
        dest="memory_type",
        choices=MEMORY_TYPES,
        default="note",
        help="Knowledge type; corrections receive retrieval priority",
    )
    parser.add_argument("--tag", action="append", default=[], type=_tag, help="A repeatable search tag")


def build_parser() -> argparse.ArgumentParser:
    """Build the original ``learn`` parser for API-surface compatibility."""
    parser = JsonArgumentParser(
        prog="cela.sh learn",
        description="Store explicitly approved knowledge in the configured vector memory.",
    )
    parser.add_argument(
        "--content",
        required=True,
        type=_content,
        help="A note, correction, or fact; use '-' to read up to 8000 characters from stdin",
    )
    _add_write_options(parser)
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
    parser.add_argument("--type", dest="memory_type", choices=MEMORY_TYPES, help="Filter to note or correction")
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
    }


def _learn(args: argparse.Namespace, parser: argparse.ArgumentParser) -> int:
    session = _scoped_session(parser, args.scope, args.session_id)
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
            normalize_memory_type(args.memory_type),
            args.tag,
            source="explicit_cli_learning",
        )
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
        "type": args.memory_type,
        "tags": metadata["tags"],
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
        base_metadata = _write_metadata(
            args.scope,
            normalize_memory_type(args.memory_type),
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
        "type": args.memory_type,
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


_COMMANDS = {
    "learn": (build_parser, _learn),
    "learn-file": (build_file_parser, _learn_file),
    "search": (build_search_parser, _search),
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
