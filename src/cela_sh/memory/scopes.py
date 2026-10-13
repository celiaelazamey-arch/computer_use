"""Session-key mapping for project-private and operator-wide memory scopes."""

GLOBAL_MEMORY_SESSION_ID = "__cela_global_memory__"
MEMORY_SCOPES = ("project", "global")
SEARCH_SCOPES = ("project", "global", "both")


def project_session_id(session_id: str | None) -> str:
    """Validate a project session selector and keep the global key reserved."""
    if not isinstance(session_id, str) or not session_id.strip():
        raise ValueError("session_id is required for project memory")
    clean_session = session_id.strip()
    if len(clean_session) > 256:
        raise ValueError("session_id must contain no more than 256 characters")
    if clean_session == GLOBAL_MEMORY_SESSION_ID:
        raise ValueError("session_id is reserved for global memory")
    return clean_session


def memory_session_id(scope: str, session_id: str | None = None) -> str:
    """Resolve a learn/search scope to the physical session key used by stores."""
    if scope == "global":
        if session_id is not None:
            raise ValueError("session_id must be omitted for global memory")
        return GLOBAL_MEMORY_SESSION_ID
    if scope == "project":
        return project_session_id(session_id)
    raise ValueError("scope must be 'project' or 'global'")


def search_session_ids(scope: str, session_id: str | None = None) -> list[tuple[str, str]]:
    """Return ``(scope, store_session_id)`` pairs for a browse/search request."""
    if scope == "global":
        return [("global", memory_session_id("global", session_id))]
    if scope == "project":
        return [("project", memory_session_id("project", session_id))]
    if scope == "both":
        project_id = project_session_id(session_id)
        return [("project", project_id), ("global", GLOBAL_MEMORY_SESSION_ID)]
    raise ValueError("scope must be 'project', 'global', or 'both'")
