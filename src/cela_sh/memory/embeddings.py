import math
from abc import ABC, abstractmethod

import requests

from ..ollama_url import validate_ollama_url
from ..thread_sessions import ThreadLocalSession
from .base import MemoryStoreError


class EmbeddingProvider(ABC):
    @abstractmethod
    def embed(self, text: str) -> list[float]:
        raise NotImplementedError


class OllamaEmbeddingProvider(EmbeddingProvider):
    def __init__(
        self,
        model: str = "nomic-embed-text",
        base_url: str = "http://127.0.0.1:11434",
        timeout: tuple[float, float] = (3.05, 60.0),
        *,
        allow_container_host: bool = False,
    ) -> None:
        if not model.strip():
            raise ValueError("embedding model must not be blank")
        validate_ollama_url(base_url, allow_container_host=allow_container_host)

        self.model = model
        self.url = f"{base_url.rstrip('/')}/api/embed"
        self.timeout = timeout
        # Shared by concurrent request threads: per-thread sessions, one handle.
        self._session = ThreadLocalSession()
        self._session.trust_env = False

    def embed(self, text: str) -> list[float]:
        if not text.strip() or len(text) > 8_000:
            raise ValueError("embedding input must contain 1 to 8000 characters")

        try:
            response = self._session.post(
                self.url,
                json={"model": self.model, "input": text},
                timeout=self.timeout,
                allow_redirects=False,
            )
            if 300 <= response.status_code < 400:
                raise MemoryStoreError("Ollama embedding redirects are not allowed.")
            response.raise_for_status()
        except requests.Timeout as exc:
            raise MemoryStoreError("Ollama embedding request timed out.") from exc
        except requests.HTTPError as exc:
            raise MemoryStoreError("Ollama embedding request returned an unsuccessful HTTP status.") from exc
        except requests.RequestException as exc:
            raise MemoryStoreError("Could not contact local Ollama embeddings.") from exc

        try:
            body = response.json()
        except requests.JSONDecodeError as exc:
            raise MemoryStoreError("Ollama returned invalid embedding JSON.") from exc

        embeddings = body.get("embeddings") if isinstance(body, dict) else None
        if not isinstance(embeddings, list) or len(embeddings) != 1 or not isinstance(embeddings[0], list):
            raise MemoryStoreError("Ollama response did not contain one embedding vector.")

        raw_vector = embeddings[0]
        if not raw_vector or any(
            isinstance(value, bool) or not isinstance(value, (int, float)) for value in raw_vector
        ):
            raise MemoryStoreError("Ollama returned an invalid embedding vector.")
        try:
            vector = [float(value) for value in raw_vector]
        except (OverflowError, ValueError) as exc:
            raise MemoryStoreError("Ollama returned an invalid embedding vector.") from exc
        if any(not math.isfinite(value) for value in vector):
            raise MemoryStoreError("Ollama returned an invalid embedding vector.")
        return vector
