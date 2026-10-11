"""Registration point for optional memory backends.

Extending memory is additive. Implement :class:`~cela_sh.memory.base.MemoryStore`
in a new module, then call :func:`register_memory_backend` with a builder that
receives the runtime settings and the ``(connect, read)`` timeout pair. No
existing module, setting name, or call site has to be edited, and every backend
is held to the same contract by ``tests/contract_memory_store.py``.

The registry is the single source of truth for the accepted
``CELA_MEMORY_BACKEND`` values: :mod:`cela_sh.settings` validates against
:func:`memory_backend_names`, and :func:`cela_sh.factory.create_planner` builds
through :func:`build_memory_store`.
"""

from collections.abc import Callable
from typing import Protocol

from .base import MemoryStore

DISABLED_BACKEND = "disabled"

#: Backends this repository ships. Names are data only, so settings validation
#: never has to import an adapter (or its HTTP client) to answer a question.
BUILTIN_MEMORY_BACKENDS = frozenset({DISABLED_BACKEND, "qdrant"})


class MemoryBackendSettings(Protocol):
    """The settings fields a backend builder is allowed to read."""

    embedding_model: str
    memory_limit: int
    ollama_url: str
    qdrant_api_key: str | None
    qdrant_collection: str
    qdrant_url: str


MemoryBackendBuilder = Callable[[MemoryBackendSettings, tuple[float, float]], MemoryStore]

_BUILDERS: dict[str, MemoryBackendBuilder] = {}


def _normalise(name: str) -> str:
    if not isinstance(name, str) or not name.strip():
        raise ValueError("A memory backend name must be a non-blank string.")
    return name.strip().lower()


def register_memory_backend(name: str, builder: MemoryBackendBuilder, *, replace: bool = False) -> None:
    """Register ``builder`` as the factory for the backend called ``name``.

    Names are compared case-insensitively. Registering the same builder twice is
    a no-op; registering a different builder over an existing name requires
    ``replace=True`` so an accidental collision fails loudly instead of quietly
    changing which store a deployment gets.
    """
    key = _normalise(name)
    if key == DISABLED_BACKEND:
        raise ValueError("The 'disabled' backend is reserved and never stores anything.")
    if not callable(builder):
        raise TypeError("A memory backend builder must be callable.")
    current = _BUILDERS.get(key)
    if current is not None and current is not builder and not replace:
        raise ValueError(f"Memory backend {key!r} is already registered; pass replace=True to override it.")
    _BUILDERS[key] = builder


def unregister_memory_backend(name: str) -> bool:
    """Remove a backend registration. Returns True when something was removed.

    Intended for tests and for extensions that are torn down at runtime.
    Built-in names stay accepted by settings validation either way.
    """
    return _BUILDERS.pop(_normalise(name), None) is not None


def memory_backend_names() -> tuple[str, ...]:
    """Every value ``CELA_MEMORY_BACKEND`` accepts, sorted and de-duplicated."""
    return tuple(sorted(BUILTIN_MEMORY_BACKENDS | frozenset(_BUILDERS)))


def registered_memory_backends() -> tuple[str, ...]:
    """Names that have a builder and therefore return a store when enabled."""
    return tuple(sorted(_BUILDERS))


def is_known_memory_backend(name: object) -> bool:
    """True when ``name`` is an accepted ``CELA_MEMORY_BACKEND`` value."""
    return isinstance(name, str) and name.strip().lower() in memory_backend_names()


def satisfies_memory_contract(candidate: object) -> bool:
    """True when ``candidate`` exposes the two :class:`MemoryStore` operations.

    The check is structural rather than ``isinstance``-based on purpose: an
    extension may subclass ``MemoryStore`` or simply implement its contract, and
    test doubles keep working. It still rejects ``None``, plain data, and
    half-finished adapters at start-up instead of during planning.
    """
    if isinstance(candidate, MemoryStore):
        return True
    return callable(getattr(candidate, "add_memory", None)) and callable(getattr(candidate, "search_memory", None))


def build_memory_store(
    name: str,
    settings: MemoryBackendSettings,
    timeout: tuple[float, float],
) -> MemoryStore:
    """Build the store for an enabled backend.

    Raises ``ConfigurationError`` for an unknown name and ``TypeError`` for a
    builder that does not return a :class:`MemoryStore`, so a broken extension
    fails at start-up instead of degrading planning silently.
    """
    from ..settings import ConfigurationError  # imported late: settings validates through this module

    key = _normalise(name)
    if key == DISABLED_BACKEND:
        raise ValueError("The 'disabled' backend has no store; only build a store for an enabled backend.")

    builder = _BUILDERS.get(key)
    if builder is None and key in BUILTIN_MEMORY_BACKENDS:
        import cela_sh.factory  # imported late: it registers the built-in builders on first use

        builder = _BUILDERS.get(key)
    if builder is None:
        raise ConfigurationError(
            f"CELA_MEMORY_BACKEND {key!r} has no registered builder. Known backends: "
            f"{', '.join(memory_backend_names())}."
        )

    store = builder(settings, timeout)
    if not satisfies_memory_contract(store):
        raise TypeError(
            f"Memory backend {key!r} must build an object implementing the MemoryStore contract "
            "(add_memory and search_memory)."
        )
    return store
