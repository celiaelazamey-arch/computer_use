from .memory import OllamaEmbeddingProvider, QdrantMemoryStore
from .planner.client import OllamaPlanner
from .settings import Settings


def create_planner(settings: Settings | None = None, model: str | None = None) -> OllamaPlanner:
    config = settings or Settings.from_env()
    timeout = (config.connect_timeout, config.read_timeout)
    memory_store = None

    if config.memory_backend == "qdrant":
        embeddings = OllamaEmbeddingProvider(
            model=config.embedding_model,
            base_url=config.ollama_url,
            timeout=timeout,
        )
        memory_store = QdrantMemoryStore(
            collection_name=config.qdrant_collection,
            qdrant_url=config.qdrant_url,
            embedding_provider=embeddings,
            timeout=timeout,
            api_key=config.qdrant_api_key,
        )

    return OllamaPlanner(
        model=model or config.planner_model,
        timeout=timeout,
        memory_store=memory_store,
        memory_limit=config.memory_limit,
        base_url=config.ollama_url,
    )
