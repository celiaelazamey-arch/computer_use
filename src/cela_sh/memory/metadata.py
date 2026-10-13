"""Validation and ranking helpers for structured, user-approved memory records."""

import re
from collections.abc import Sequence
from typing import Any

MEMORY_TYPES = ("note", "correction", "decision", "failure", "debt")
DECISION_STATUSES = ("active", "superseded", "deprecated", "rejected")
MAX_TAGS = 20
MAX_TAG_LENGTH = 64
_TAG_PATTERN = re.compile(r"[\w.-]+", flags=re.UNICODE)


def normalize_memory_type(value: str | None) -> str:
    """Return a supported record type, defaulting legacy records to ``note``."""
    if value is None:
        return "note"
    if not isinstance(value, str) or value not in MEMORY_TYPES:
        raise ValueError(f"type must be one of: {', '.join(MEMORY_TYPES)}")
    return value


def normalize_decision_status(value: str | None) -> str:
    """Return a supported decision lifecycle status, defaulting to ``active``."""
    if value is None:
        return "active"
    if not isinstance(value, str) or value not in DECISION_STATUSES:
        raise ValueError(f"status must be one of: {', '.join(DECISION_STATUSES)}")
    return value


def normalize_tags(tags: Sequence[str] | None) -> list[str]:
    """Validate, case-fold, de-duplicate, and sort a bounded set of tags."""
    if tags is None:
        return []
    if isinstance(tags, (str, bytes)) or not isinstance(tags, Sequence):
        raise ValueError("tags must be a list of strings")
    if len(tags) > MAX_TAGS:
        raise ValueError(f"at most {MAX_TAGS} tags are allowed")

    normalized = set()
    for tag in tags:
        if not isinstance(tag, str):
            raise ValueError("each tag must be a string")
        clean_tag = tag.strip().casefold()
        if not clean_tag or len(clean_tag) > MAX_TAG_LENGTH or _TAG_PATTERN.fullmatch(clean_tag) is None:
            raise ValueError("tags must be 1 to 64 letters, digits, dots, dashes, or underscores")
        normalized.add(clean_tag)
    return sorted(normalized)


def validate_memory_filters(
    limit: int,
    tags: Sequence[str] | None,
    memory_type: str | None,
) -> tuple[int, list[str], str | None]:
    """Validate and normalize list filters shared by every built-in backend."""
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 1_000:
        raise ValueError("limit must be an integer between 1 and 1000")
    clean_tags = normalize_tags(tags)
    clean_type = normalize_memory_type(memory_type) if memory_type is not None else None
    return limit, clean_tags, clean_type


def metadata_matches(
    metadata: Any,
    *,
    tags: Sequence[str] | None = None,
    memory_type: str | None = None,
) -> bool:
    """Check whether metadata contains every requested tag and the requested type."""
    if not isinstance(metadata, dict):
        return False
    required_tags = normalize_tags(tags)
    if required_tags:
        try:
            stored_tags = set(normalize_tags(metadata.get("tags", [])))
        except ValueError:
            return False
        if not set(required_tags) <= stored_tags:
            return False
    if memory_type is not None and metadata.get("type", "note") != normalize_memory_type(memory_type):
        return False
    return True


def correction_first(memories: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Sort corrections before notes while preserving relevance within each type."""
    return sorted(
        memories,
        key=lambda memory: (
            isinstance(memory.get("metadata"), dict) and memory["metadata"].get("type") == "correction",
            float(memory.get("score", 0.0)) if isinstance(memory.get("score", 0.0), (int, float)) else 0.0,
        ),
        reverse=True,
    )
