"""Validation for source identities and idempotent file-memory replacement."""

from collections.abc import Sequence
from copy import deepcopy
from hashlib import sha256
import json
import re
from pathlib import PurePosixPath
from typing import Any

from .scopes import GLOBAL_MEMORY_SESSION_ID

MAX_SOURCE_PATH_LENGTH = 1_024
MAX_SOURCE_CHUNKS = 250
_SOURCE_HASH_PATTERN = re.compile(r"[0-9a-f]{64}")


def content_hash(content: str) -> str:
    """Hash the exact UTF-8 content that is stored for a memory chunk."""
    if not isinstance(content, str):
        raise ValueError("memory content must be text")
    return sha256(content.encode("utf-8")).hexdigest()


def normalize_source_path(value: str) -> str:
    """Validate a stable, relative POSIX source path without leaking host paths."""
    if not isinstance(value, str) or not value.strip():
        raise ValueError("source_path must not be blank")
    source_path = value.strip()
    if (
        len(source_path) > MAX_SOURCE_PATH_LENGTH
        or "\x00" in source_path
        or "\\" in source_path
        or source_path.startswith("/")
        or re.match(r"^[A-Za-z]:", source_path)
    ):
        raise ValueError("source_path must be a bounded relative POSIX path")
    parsed = PurePosixPath(source_path)
    if any(part in {"", ".", ".."} for part in parsed.parts):
        raise ValueError("source_path must not contain empty or parent path segments")
    normalized = parsed.as_posix()
    if not normalized or normalized == ".":
        raise ValueError("source_path must not be blank")
    return normalized


def validate_source_replacement(
    session_id: str,
    source_path: str,
    source_hash: str,
    chunks: Sequence[tuple[str, dict[str, Any]]],
) -> tuple[str, str, str, list[tuple[str, dict[str, Any]]]]:
    """Validate an entire source replacement before any backend mutates data."""
    if not isinstance(session_id, str) or not session_id.strip():
        raise ValueError("session_id must not be blank")
    clean_session = session_id.strip()
    clean_path = normalize_source_path(source_path)
    if not isinstance(source_hash, str) or _SOURCE_HASH_PATTERN.fullmatch(source_hash) is None:
        raise ValueError("source_hash must be a lowercase SHA-256 hex digest")
    if isinstance(chunks, (str, bytes)) or not isinstance(chunks, Sequence):
        raise ValueError("chunks must be a sequence of content and metadata pairs")
    if not 1 <= len(chunks) <= MAX_SOURCE_CHUNKS:
        raise ValueError(f"source replacement requires 1 to {MAX_SOURCE_CHUNKS} chunks")

    expected_count = len(chunks)
    validated: list[tuple[str, dict[str, Any]]] = []
    for index, record in enumerate(chunks, start=1):
        if not isinstance(record, tuple) or len(record) != 2:
            raise ValueError("each source chunk must be a (content, metadata) pair")
        content, metadata = record
        if not isinstance(content, str) or not content.strip() or len(content) > 8_000:
            raise ValueError("source chunks must contain 1 to 8000 characters")
        if not isinstance(metadata, dict):
            raise ValueError("source chunk metadata must be a dictionary")
        clean_metadata = deepcopy(metadata)
        if (
            clean_metadata.get("source_path") != clean_path
            or clean_metadata.get("source_hash") != source_hash
            or clean_metadata.get("content_hash") != content_hash(content)
            or clean_metadata.get("chunk_index") != index
            or clean_metadata.get("chunk_count") != expected_count
            or clean_metadata.get("session_id", clean_session) != clean_session
        ):
            raise ValueError("source chunk metadata does not match its replacement identity")
        scope = clean_metadata.get("scope")
        if scope not in {"project", "global"}:
            raise ValueError("source chunks must declare a project or global scope")
        if scope == "global" and clean_session != GLOBAL_MEMORY_SESSION_ID:
            raise ValueError("global source metadata must use the reserved global session")
        if scope == "project" and clean_session == GLOBAL_MEMORY_SESSION_ID:
            raise ValueError("project source metadata must not use the reserved global session")
        imported_at = clean_metadata.get("imported_at")
        if not isinstance(imported_at, str) or not imported_at:
            raise ValueError("source chunk metadata must include imported_at")
        if validated and clean_metadata.get("imported_at") != validated[0][1].get("imported_at"):
            raise ValueError("all source chunks must share one imported_at timestamp")
        try:
            json.dumps(clean_metadata, ensure_ascii=False, allow_nan=False)
        except (TypeError, ValueError) as exc:
            raise ValueError("source chunk metadata must contain JSON-compatible values") from exc
        validated.append((content, clean_metadata))
    return clean_session, clean_path, source_hash, validated
