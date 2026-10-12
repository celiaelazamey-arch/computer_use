import math
import os
from dataclasses import dataclass

from .memory.registry import memory_backend_names


class ConfigurationError(ValueError):
    """Raised when runtime settings are missing or invalid."""


def _positive_float(name: str, default: float) -> float:
    raw = os.environ.get(name)
    try:
        value = default if raw is None else float(raw)
    except ValueError as exc:
        raise ConfigurationError(f"{name} must be a number.") from exc
    if not math.isfinite(value) or value <= 0:
        raise ConfigurationError(f"{name} must be a positive finite number.")
    return value


def _positive_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    try:
        value = default if raw is None else int(raw)
    except ValueError as exc:
        raise ConfigurationError(f"{name} must be a positive integer.") from exc
    if value < 1:
        raise ConfigurationError(f"{name} must be a positive integer.")
    return value


@dataclass(frozen=True, slots=True)
class Settings:
    ollama_url: str = "http://127.0.0.1:11434"
    planner_model: str = "qwen2.5:3b"
    embedding_model: str = "nomic-embed-text"
    memory_backend: str = "disabled"
    memory_limit: int = 3
    sqlite_vec_path: str = "cela_memory.db"
    qdrant_url: str = "http://127.0.0.1:6333"
    qdrant_collection: str = "agent_memory"
    qdrant_api_key: str | None = None
    connect_timeout: float = 3.05
    read_timeout: float = 60.0

    def __post_init__(self) -> None:
        if not self.ollama_url.strip():
            raise ConfigurationError("CELA_OLLAMA_URL must not be blank.")
        if not self.planner_model.strip() or not self.embedding_model.strip():
            raise ConfigurationError("Model names must not be blank.")
        if self.memory_backend not in memory_backend_names():
            valid = ", ".join(f"'{name}'" for name in memory_backend_names())
            given = repr(self.memory_backend[:40])
            raise ConfigurationError(f"CELA_MEMORY_BACKEND {given} is not supported. Valid backends: {valid}.")
        if self.memory_backend == "qdrant" and (
            not self.qdrant_url.strip() or not self.qdrant_collection.strip()
        ):
            raise ConfigurationError("Qdrant URL and collection must not be blank when memory is enabled.")
        if self.memory_backend == "sqlite-vec" and not self.sqlite_vec_path.strip():
            raise ConfigurationError("CELA_SQLITE_VEC_PATH must not be blank when sqlite-vec backend is enabled.")
        if self.qdrant_api_key is not None and not self.qdrant_api_key.strip():
            raise ConfigurationError("CELA_QDRANT_API_KEY must not be blank when set.")
        if isinstance(self.memory_limit, bool) or not isinstance(self.memory_limit, int) or self.memory_limit < 1:
            raise ConfigurationError("Memory limit must be a positive integer.")
        if (
            isinstance(self.connect_timeout, bool)
            or not isinstance(self.connect_timeout, (int, float))
            or not math.isfinite(self.connect_timeout)
            or self.connect_timeout <= 0
        ):
            raise ConfigurationError("Connect timeout must be a positive finite number.")
        if (
            isinstance(self.read_timeout, bool)
            or not isinstance(self.read_timeout, (int, float))
            or not math.isfinite(self.read_timeout)
            or self.read_timeout <= 0
        ):
            raise ConfigurationError("Read timeout must be a positive finite number.")

    @classmethod
    def from_env(cls) -> "Settings":
        api_key = os.environ.get("CELA_QDRANT_API_KEY")
        return cls(
            ollama_url=os.environ.get("CELA_OLLAMA_URL", "http://127.0.0.1:11434").strip(),
            planner_model=os.environ.get("CELA_PLANNER_MODEL", "qwen2.5:3b").strip(),
            embedding_model=os.environ.get("CELA_EMBEDDING_MODEL", "nomic-embed-text").strip(),
            memory_backend=os.environ.get("CELA_MEMORY_BACKEND", "disabled").strip().lower(),
            memory_limit=_positive_int("CELA_MEMORY_LIMIT", 3),
            sqlite_vec_path=os.environ.get("CELA_SQLITE_VEC_PATH", "cela_memory.db").strip(),
            qdrant_url=os.environ.get("CELA_QDRANT_URL", "http://127.0.0.1:6333").strip(),
            qdrant_collection=os.environ.get("CELA_QDRANT_COLLECTION", "agent_memory").strip(),
            qdrant_api_key=api_key.strip() if api_key and api_key.strip() else None,
            connect_timeout=_positive_float("CELA_CONNECT_TIMEOUT", 3.05),
            read_timeout=_positive_float("CELA_READ_TIMEOUT", 60.0),
        )
