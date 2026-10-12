from .memory import (
    DISABLED_BACKEND,
    MemoryStore,
    OllamaEmbeddingProvider,
    QdrantMemoryStore,
    SqliteVecMemoryStore,
    build_memory_store,
    register_memory_backend,
)
from .planner import (
    DEFAULT_PLANNER_PROVIDER,
    OllamaPlanner,
    Planner,
    build_planner,
    register_planner_provider,
)
from .settings import Settings


def _build_qdrant_store(config: Settings, timeout: tuple[float, float]) -> QdrantMemoryStore:
    """Built-in ``qdrant`` backend: local embeddings plus the Qdrant REST adapter."""
    embeddings = OllamaEmbeddingProvider(
        model=config.embedding_model,
        base_url=config.ollama_url,
        timeout=timeout,
        **({"allow_container_host": True} if getattr(config, "allow_ollama_container_host", False) is True else {}),
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
        **({"allow_container_host": True} if getattr(config, "allow_ollama_container_host", False) is True else {}),
    )
    return SqliteVecMemoryStore(
        db_path=config.sqlite_vec_path,
        embedding_provider=embeddings,
        timeout=timeout[1],  # read timeout
    )


def _build_ollama_planner(
    config: Settings,
    timeout: tuple[float, float],
    memory_store: MemoryStore | None = None,
    model: str | None = None,
) -> OllamaPlanner:
    """Built-in ``ollama`` planner provider: local loopback Ollama client."""
    return OllamaPlanner(
        model=model or config.planner_model,
        timeout=timeout,
        memory_store=memory_store,
        memory_limit=config.memory_limit,
        base_url=config.ollama_url,
        **({"allow_container_host": True} if getattr(config, "allow_ollama_container_host", False) is True else {}),
    )


# Registering here keeps the adapter names in this module's namespace, so tests
# and callers can still patch or replace them without touching the registry.
register_memory_backend("qdrant", _build_qdrant_store, replace=True)
register_memory_backend("sqlite-vec", _build_sqlite_vec_store, replace=True)
register_planner_provider(DEFAULT_PLANNER_PROVIDER, _build_ollama_planner, replace=True)


def create_planner(settings: Settings | None = None, model: str | None = None) -> Planner:
    """Composition root: pick the planner provider/model and inject the enabled memory backend.

    New memory backends and local planner providers are added by registering a
    builder (see :mod:`cela_sh.memory.registry` and
    :mod:`cela_sh.planner.registry`); this function does not need to change.
    """
    config = settings or Settings.from_env()
    timeout = (config.connect_timeout, config.read_timeout)
    memory_store = None

    if config.memory_backend != DISABLED_BACKEND:
        memory_store = build_memory_store(config.memory_backend, config, timeout)

    provider_name = getattr(config, "planner_provider", DEFAULT_PLANNER_PROVIDER)
    return build_planner(provider_name, config, timeout, memory_store=memory_store, model=model)
