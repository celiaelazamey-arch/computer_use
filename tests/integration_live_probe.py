"""Opt-in smoke test for local Ollama embeddings and Qdrant session isolation."""

import sys
from uuid import uuid4

import requests

from cela_sh.memory import OllamaEmbeddingProvider, QdrantMemoryStore
from cela_sh.settings import ConfigurationError, Settings


def _check_status(response: requests.Response) -> None:
    if 300 <= response.status_code < 400:
        raise RuntimeError("Local service redirected a request; redirects are not allowed.")
    response.raise_for_status()


def _probe_services(
    settings: Settings,
    embeddings: OllamaEmbeddingProvider,
    store: QdrantMemoryStore,
    ollama_http: requests.Session,
    qdrant_http: requests.Session,
) -> None:
    timeout = (settings.connect_timeout, settings.read_timeout)
    ollama_url = settings.ollama_url.rstrip("/")
    qdrant_url = settings.qdrant_url.rstrip("/")
    tags_response = ollama_http.get(f"{ollama_url}/api/tags", timeout=timeout, allow_redirects=False)
    _check_status(tags_response)
    try:
        models = tags_response.json()["models"]
    except (requests.JSONDecodeError, KeyError, TypeError) as exc:
        raise RuntimeError("Ollama returned an invalid model list.") from exc
    if not isinstance(models, list) or not any(
        isinstance(model, dict)
        and isinstance(model.get("name"), str)
        and (
            model["name"] == settings.embedding_model
            or (
                ":" not in settings.embedding_model
                and model["name"].startswith(f"{settings.embedding_model}:")
            )
        )
        for model in models
    ):
        raise RuntimeError(f"Ollama model {settings.embedding_model!r} is not installed.")

    vector = embeddings.embed("cela.sh live integration probe")
    print(f"PASS Ollama health and {settings.embedding_model} embeddings ({len(vector)} dimensions)")

    qdrant_health = qdrant_http.get(f"{qdrant_url}/healthz", timeout=timeout, allow_redirects=False)
    _check_status(qdrant_health)

    token = uuid4().hex
    session_alpha = f"probe-alpha-{token}"
    session_beta = f"probe-beta-{token}"
    memory_alpha = f"{token}: crimson observatory stores the code ORBIT-3921"
    memory_beta = f"{token}: glacier bakery stores the recipe JUNIPER-8476"
    store.add_memory(session_alpha, memory_alpha, {"probe": True})
    store.add_memory(session_beta, memory_beta, {"probe": True})

    alpha_results = store.search_memory(memory_alpha, session_alpha, limit=1)
    if len(alpha_results) != 1 or alpha_results[0]["content"] != memory_alpha:
        raise RuntimeError("Qdrant did not return the memory for its owning session.")

    beta_results = store.search_memory(memory_alpha, session_beta, limit=1)
    if len(beta_results) != 1 or beta_results[0]["content"] != memory_beta:
        raise RuntimeError("Qdrant session filter did not isolate the other session.")
    print("PASS Qdrant add, search, and session isolation")


def _delete_probe_collection(
    collection_name: str,
    completed: bool,
    settings: Settings,
    http: requests.Session,
) -> None:
    url = f"{settings.qdrant_url.rstrip('/')}/collections/{collection_name}"
    timeout = (settings.connect_timeout, settings.read_timeout)
    response = http.delete(url, timeout=timeout, allow_redirects=False)
    if response.status_code == 404:
        if completed:
            raise RuntimeError("The temporary Qdrant collection disappeared before cleanup.")
    else:
        _check_status(response)

    verify = http.get(url, timeout=timeout, allow_redirects=False)
    if verify.status_code != 404:
        _check_status(verify)
        raise RuntimeError("The temporary Qdrant collection still exists after cleanup.")
    print("PASS temporary Qdrant collection deleted")


def main() -> int:
    try:
        settings = Settings.from_env()
    except ConfigurationError as exc:
        print(f"FAIL live probe configuration: {exc}", file=sys.stderr)
        return 1

    collection_name = f"live_probe_{uuid4().hex}"
    timeout = (settings.connect_timeout, settings.read_timeout)
    try:
        embeddings = OllamaEmbeddingProvider(
            model=settings.embedding_model,
            base_url=settings.ollama_url,
            timeout=timeout,
        )
        store = QdrantMemoryStore(
            collection_name=collection_name,
            qdrant_url=settings.qdrant_url,
            embedding_provider=embeddings,
            timeout=timeout,
            api_key=settings.qdrant_api_key,
        )
    except ValueError as exc:
        print(f"FAIL live probe configuration: {exc}", file=sys.stderr)
        return 1

    ollama_http = requests.Session()
    ollama_http.trust_env = False
    qdrant_http = requests.Session()
    qdrant_http.trust_env = False
    if settings.qdrant_api_key:
        qdrant_http.headers.update({"api-key": settings.qdrant_api_key})
    probe_error = None
    cleanup_error = None
    completed = False

    try:
        try:
            _probe_services(settings, embeddings, store, ollama_http, qdrant_http)
            completed = True
        except Exception as exc:
            probe_error = exc
    finally:
        try:
            _delete_probe_collection(collection_name, completed, settings, qdrant_http)
        except Exception as exc:
            cleanup_error = exc
        finally:
            ollama_http.close()
            qdrant_http.close()

    if probe_error:
        print(f"FAIL live probe: {probe_error}", file=sys.stderr)
    if cleanup_error:
        print(f"FAIL temporary collection cleanup: {cleanup_error}", file=sys.stderr)
    return 1 if probe_error or cleanup_error else 0


if __name__ == "__main__":
    raise SystemExit(main())
