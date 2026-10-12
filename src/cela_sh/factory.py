from .memory import (
    DISABLED_BACKEND,
    OllamaEmbeddingProvider,
    QdrantMemoryStore,
    SqliteVecMemoryStore,
    build_memory_store,
    register_memory_backend,
)
from .planner.client import OllamaPlanner
from .settings import Settings


def _build_qdrant_store(config: Settings, timeout: tuple[float, float]) -> QdrantMemoryStore:
    """Built-in ``qdrant`` backend: local embeddings plus the Qdrant REST adapter."""
    embeddings = OllamaEmbeddingProvider(
        model=config.embedding_model,
        base_url=config.ollama_url,
        timeout=timeout,
    )
    return QdrantMemoryStore(
        collection_name=config.qdrant_collection,
        qdrant_url=config.qdrant_url,
        embedding_provider=embeddings,
        timeout=timeout,
        api_key=config.qdrant_api_key,
    )


def _build_sqlite_vec_store(config: Settings, timeout: tuple[float, float]) -> SqliteVecMemoryStore:
    """Built-in ``sqlite-vec`` backend: local SQLite file with vector embeddings."""
    embeddings = OllamaEmbeddingProvider(
        model=config.embedding_model,
        base_url=config.ollama_url,
        timeout=timeout,
    )
    return SqliteVecMemoryStore(
        db_path=config.sqlite_vec_path,
        embedding_provider=embeddings,
        timeout=timeout[1],  # read timeout
    )


# Registering here keeps the adapter names in this module's namespace, so tests
# and callers can still patch or replace them without touching the registry.
register_memory_backend("qdrant", _build_qdrant_store, replace=True)
register_memory_backend("sqlite-vec", _build_sqlite_vec_store, replace=True)


def create_planner(settings: Settings | None = None, model: str | None = None) -> OllamaPlanner:
    """Composition root: pick the planner model and inject the enabled memory backend.

    New backends are added by registering a builder (see
    :mod:`cela_sh.memory.registry`); this function does not need to change.
    """
    config = settings or Settings.from_env()
    timeout = (config.connect_timeout, config.read_timeout)
    memory_store = None

    if config.memory_backend != DISABLED_BACKEND:
        memory_store = build_memory_store(config.memory_backend, config, timeout)

    return OllamaPlanner(
        model=model or config.planner_model,
        timeout=timeout,
        memory_store=memory_store,
        memory_limit=config.memory_limit,
        base_url=config.ollama_url,
    )
