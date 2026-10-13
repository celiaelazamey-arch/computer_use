"""Local-first HTTP API with optional shared bearer authentication.

Run with ``uvicorn cela_sh.server:app`` after installing ``cela-sh[server]``.
Set ``CELA_API_KEY`` to require a bearer token on planning and memory routes;
cloud deployments must set it and terminate TLS at a trusted ingress. Session
IDs remain selectors, not credentials or per-user authorization.

Plans remain dry-run and never write memory. ``/health`` is public liveness only.

Every failure is answered with a stable code and a fixed sentence. Unexpected
exceptions become a sanitized ``500`` and are logged server-side; exception text,
types, and tracebacks are never returned to the caller.
"""

import hmac
import logging
import math
from contextlib import asynccontextmanager
from threading import Lock
from typing import Any, Literal

from fastapi import Depends, FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import Field, model_validator
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from .factory import create_planner
from .memory import MemoryStore, MemoryStoreError
from .memory.metadata import correction_first, metadata_matches, normalize_tags
from .memory.scopes import memory_session_id, search_session_ids
from .planner.base import Planner
from .planner.client import (
    PlannerConnectionError,
    PlannerError,
    PlannerMemoryError,
    PlannerResponseError,
    PlannerValidationError,
)
from .planner.models import Plan, PlannerInput, StrictModel
from .settings import Settings

_logger = logging.getLogger(__name__)
_bearer_scheme = HTTPBearer(auto_error=False)
_FILE_SOURCE_FIELDS = frozenset(
    {
        "source_name",
        "source_path",
        "source_hash",
        "source_sha256",
        "content_hash",
        "imported_at",
        "chunk_index",
        "chunk_count",
    }
)


class _APIAuthenticationError(Exception):
    """Raised when an enabled API key is absent or does not match."""


def _require_api_key(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer_scheme),
) -> None:
    """Require the configured shared key without exposing it in responses/logs."""
    expected = request.app.state.settings.api_key
    if expected is None:
        return

    supplied = (
        credentials.credentials
        if credentials is not None and credentials.scheme.lower() == "bearer"
        else ""
    )
    if not hmac.compare_digest(supplied.encode("utf-8"), expected.encode("utf-8")):
        raise _APIAuthenticationError


class PlanRequest(PlannerInput):
    session_id: str | None = Field(default=None, min_length=1, max_length=256)

    @model_validator(mode="after")
    def session_is_not_blank(self):
        if self.session_id is not None and not self.session_id.strip():
            raise ValueError("session_id must not be blank")
        return self


class MemoryRequest(StrictModel):
    session_id: str | None = Field(default=None, min_length=1, max_length=256)
    scope: Literal["project", "global"] = "project"
    content: str = Field(min_length=1, max_length=8_000)
    type: Literal["note", "correction"] = "note"
    tags: list[str] = Field(default_factory=list, max_length=20)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def fields_are_valid(self):
        if not self.content.strip():
            raise ValueError("content must not be blank")
        memory_session_id(self.scope, self.session_id)
        normalize_tags(self.tags)
        return self


class MemorySearchRequest(StrictModel):
    session_id: str | None = Field(default=None, min_length=1, max_length=256)
    scope: Literal["project", "global", "both"] = "project"
    query: str | None = Field(default=None, min_length=1, max_length=8_000)
    type: Literal["note", "correction"] | None = None
    tags: list[str] = Field(default_factory=list, max_length=20)
    limit: int = Field(default=3, ge=1, le=100)

    @model_validator(mode="after")
    def fields_are_valid(self):
        if self.query is not None and not self.query.strip():
            raise ValueError("query must not be blank")
        search_session_ids(self.scope, self.session_id)
        normalize_tags(self.tags)
        return self


class MemoryResult(StrictModel):
    session_id: str | None
    scope: Literal["project", "global"]
    content: str
    metadata: dict[str, Any]
    score: float = Field(allow_inf_nan=False)


def _error(
    status: int,
    code: str,
    message: str,
    *,
    headers: dict[str, str] | None = None,
) -> JSONResponse:
    return JSONResponse(
        status_code=status,
        headers=headers,
        content={"error": {"code": code, "message": message}},
    )


def _unexpected_error() -> JSONResponse:
    """The only 500 a caller may see: no exception type, text, path, or traceback."""
    return _error(500, "internal_error", "Internal server error.")


class UnexpectedErrorMiddleware:
    """Answer unhandled exceptions with the documented JSON envelope.

    Domain failures have typed handlers, so anything that arrives here is a bug.
    Starlette's server-error handler would either return a plain-text body or,
    with a registered handler, send its response and re-raise the exception --
    which lets a debug server or a test client surface the original detail.
    Answering inside the request path keeps the status, body, and media type
    identical for every unexpected failure, while the traceback is written to the
    log only.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        response_started = False

        async def send_and_track_start(message: Message) -> None:
            nonlocal response_started
            if message["type"] == "http.response.start":
                response_started = True
            await send(message)

        try:
            await self.app(scope, receive, send_and_track_start)
        except Exception:
            if response_started:
                # Part of the response is already on the wire and cannot be
                # replaced by a JSON body without corrupting the stream.
                raise
            _logger.exception("Unhandled error while serving %s", scope.get("path", "an unknown path"))
            await _unexpected_error()(scope, receive, send)


def create_app(settings: Settings | None = None, planner: Planner | None = None) -> FastAPI:
    """Build an app without network or database I/O until its lifespan starts."""

    @asynccontextmanager
    async def lifespan(application: FastAPI):
        config = settings or Settings.from_env()
        application.state.settings = config
        application.state.planner = planner if planner is not None else create_planner(config)
        # requests.Session is mutable and not guaranteed thread-safe. Keep the
        # shared planner/embedding sessions out of concurrent worker threads.
        application.state.backend_lock = Lock()
        try:
            yield
        finally:
            # Only close sessions owned by this app, not an injected caller's
            # planner. SQLite operation connections are already short-lived.
            if planner is None:
                active = application.state.planner
                session = getattr(active, "_session", None)
                if session is not None:
                    session.close()
                store = getattr(active, "memory_store", None)
                provider = getattr(store, "embedding_provider", None)
                session = getattr(provider, "_session", None)
                if session is not None:
                    session.close()
                session = getattr(store, "_session", None)
                if session is not None:
                    session.close()

    application = FastAPI(
        title="CELA.sh planning API",
        version="1.0.2",
        description=(
            "Local-first planning only: dry_run=true, execution_enabled=false. "
            "When CELA_API_KEY is configured, planning and memory routes require bearer authentication."
        ),
        lifespan=lifespan,
    )
    # Sits outside the domain handlers and inside Starlette's server-error
    # handler, so an unexpected failure becomes a sanitized 500 before anything
    # can re-raise it to the caller.
    application.add_middleware(UnexpectedErrorMiddleware)

    @application.exception_handler(_APIAuthenticationError)
    async def api_authentication_error_handler(request: Request, exc: _APIAuthenticationError):
        return _error(
            401,
            "authentication_required",
            "A valid bearer token is required.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    @application.exception_handler(PlannerError)
    async def planner_error_handler(request: Request, exc: PlannerError):
        if isinstance(exc, PlannerConnectionError):
            return _error(503, "planner_connection_error", "Unable to connect to planner service.")
        if isinstance(exc, PlannerResponseError):
            return _error(502, "planner_response_error", "Planner service returned an invalid response.")
        if isinstance(exc, PlannerValidationError):
            return _error(422, "planner_validation_error", "Planner input validation failed.")
        if isinstance(exc, PlannerMemoryError):
            return _error(503, "planner_memory_error", "Planner memory backend is unavailable.")
        return _error(500, "planner_error", "An internal planner error occurred.")

    @application.exception_handler(MemoryStoreError)
    async def memory_error_handler(request: Request, exc: MemoryStoreError):
        return _error(503, "memory_store_error", "Memory store is unavailable.")

    @application.get("/health", tags=["health"])
    def health():
        # Liveness only: no Ollama calls or implicit model downloads. Starting
        # the app has already validated settings and initialized its store.
        return {
            "status": "ok",
            "memory_backend": application.state.settings.memory_backend,
            "dry_run": True,
            "execution_enabled": False,
        }

    @application.post(
        "/plan",
        response_model=Plan,
        tags=["planning"],
        dependencies=[Depends(_require_api_key)],
    )
    def plan(body: PlanRequest):
        with application.state.backend_lock:
            return application.state.planner.generate_plan(
                body.goal, body.screen_context, session_id=body.session_id
            )

    def memory_store() -> MemoryStore | None:
        return application.state.planner.memory_store

    @application.post(
        "/memory",
        status_code=201,
        tags=["memory"],
        dependencies=[Depends(_require_api_key)],
    )
    def add_memory(body: MemoryRequest):
        store = memory_store()
        if store is None:
            return _error(409, "memory_disabled", "Memory is disabled in this deployment.")
        try:
            session = memory_session_id(body.scope, body.session_id)
            metadata = {
                key: value for key, value in body.metadata.items() if key not in _FILE_SOURCE_FIELDS
            }
            metadata.update(
                {
                    "scope": body.scope,
                    "type": body.type,
                    "tags": normalize_tags(body.tags),
                }
            )
            with application.state.backend_lock:
                stored = store.add_memory(session, body.content, metadata)
        except ValueError:
            return _error(422, "memory_validation_error", "Invalid memory input.")
        if stored is not True:
            raise MemoryStoreError("Memory store did not confirm the write.")
        return {"stored": True}

    @application.post(
        "/memory/search",
        response_model=list[MemoryResult],
        tags=["memory"],
        dependencies=[Depends(_require_api_key)],
    )
    def search_memory(body: MemorySearchRequest):
        store = memory_store()
        if store is None:
            return _error(409, "memory_disabled", "Memory is disabled in this deployment.")
        try:
            scopes = search_session_ids(body.scope, body.session_id)
            tags = normalize_tags(body.tags)
            results = []
            with application.state.backend_lock:
                for scope, session in scopes:
                    if body.query is None:
                        memories = store.list_memory(
                            session,
                            limit=body.limit,
                            tags=tags,
                            memory_type=body.type,
                        )
                    else:
                        candidate_limit = min(max(body.limit * 5, body.limit), 1_000)
                        memories = store.search_memory(body.query, session, limit=candidate_limit)
                        memories = [
                            memory
                            for memory in memories
                            if isinstance(memory, dict)
                            and metadata_matches(memory.get("metadata"), tags=tags, memory_type=body.type)
                        ]
                    for memory in memories:
                        if not isinstance(memory, dict) or memory.get("session_id") != session:
                            raise MemoryStoreError("Memory store returned invalid results.")
                        metadata = memory.get("metadata")
                        content = memory.get("content")
                        score = memory.get("score")
                        if (
                            not isinstance(metadata, dict)
                            or not isinstance(content, str)
                            or not content.strip()
                            or len(content) > 8_000
                            or isinstance(score, bool)
                            or not isinstance(score, (int, float))
                            or not math.isfinite(score)
                        ):
                            raise MemoryStoreError("Memory store returned invalid results.")
                        results.append(
                            {
                                "session_id": body.session_id.strip() if scope == "project" else None,
                                "scope": scope,
                                "content": content,
                                "metadata": metadata,
                                "score": float(score),
                            }
                        )
            return correction_first(results)[: body.limit]
        except ValueError:
            return _error(422, "memory_validation_error", "Invalid memory search input.")

    return application


app = create_app()
