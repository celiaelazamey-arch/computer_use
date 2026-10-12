"""Local, unauthenticated HTTP API. Plans remain dry-run and never write memory.

Run with ``uvicorn cela_sh.server:app`` after installing ``cela-sh[server]``.
Do not expose this API to untrusted networks; session IDs are selectors, not
credentials. Compose publishes it on host loopback only.
"""

from contextlib import asynccontextmanager
from threading import Lock
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from pydantic import Field, model_validator

from .factory import create_planner
from .memory import MemoryStore, MemoryStoreError
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


class PlanRequest(PlannerInput):
    session_id: str | None = Field(default=None, min_length=1, max_length=256)

    @model_validator(mode="after")
    def session_is_not_blank(self):
        if self.session_id is not None and not self.session_id.strip():
            raise ValueError("session_id must not be blank")
        return self


class MemoryRequest(StrictModel):
    session_id: str = Field(min_length=1, max_length=256)
    content: str = Field(min_length=1, max_length=8_000)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def text_is_not_blank(self):
        if not self.session_id.strip() or not self.content.strip():
            raise ValueError("session_id and content must not be blank")
        return self


class MemorySearchRequest(StrictModel):
    session_id: str = Field(min_length=1, max_length=256)
    query: str = Field(min_length=1, max_length=8_000)
    limit: int = Field(default=3, ge=1, le=100)

    @model_validator(mode="after")
    def text_is_not_blank(self):
        if not self.session_id.strip() or not self.query.strip():
            raise ValueError("session_id and query must not be blank")
        return self


class MemoryResult(StrictModel):
    session_id: str
    content: str
    metadata: dict[str, Any]
    score: float = Field(allow_inf_nan=False)


def _error(status: int, code: str, message: str) -> JSONResponse:
    return JSONResponse(status_code=status, content={"error": {"code": code, "message": message}})


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
        version="1.0.0",
        description="Local planning only: dry_run=true, execution_enabled=false. No authentication yet.",
        lifespan=lifespan,
    )

    @application.exception_handler(PlannerError)
    async def planner_error_handler(request: Request, exc: PlannerError):
        if isinstance(exc, PlannerConnectionError):
            return _error(503, "planner_connection_error", str(exc))
        if isinstance(exc, PlannerResponseError):
            return _error(502, "planner_response_error", str(exc))
        if isinstance(exc, PlannerValidationError):
            return _error(422, "planner_validation_error", str(exc))
        if isinstance(exc, PlannerMemoryError):
            return _error(503, "planner_memory_error", str(exc))
        return _error(500, "planner_error", str(exc))

    @application.exception_handler(MemoryStoreError)
    async def memory_error_handler(request: Request, exc: MemoryStoreError):
        return _error(503, "memory_store_error", str(exc))

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

    @application.post("/plan", response_model=Plan, tags=["planning"])
    def plan(body: PlanRequest):
        with application.state.backend_lock:
            return application.state.planner.generate_plan(
                body.goal, body.screen_context, session_id=body.session_id
            )

    def memory_store() -> MemoryStore | None:
        return application.state.planner.memory_store

    @application.post("/memory", status_code=201, tags=["memory"])
    def add_memory(body: MemoryRequest):
        store = memory_store()
        if store is None:
            return _error(409, "memory_disabled", "Memory is disabled in this deployment.")
        try:
            with application.state.backend_lock:
                stored = store.add_memory(body.session_id, body.content, body.metadata)
        except ValueError as exc:
            return _error(422, "memory_validation_error", str(exc))
        if stored is not True:
            raise MemoryStoreError("Memory store did not confirm the write.")
        return {"stored": True}

    @application.post("/memory/search", response_model=list[MemoryResult], tags=["memory"])
    def search_memory(body: MemorySearchRequest):
        store = memory_store()
        if store is None:
            return _error(409, "memory_disabled", "Memory is disabled in this deployment.")
        try:
            with application.state.backend_lock:
                return store.search_memory(body.query, body.session_id, limit=body.limit)
        except ValueError as exc:
            return _error(422, "memory_validation_error", str(exc))

    return application


app = create_app()
