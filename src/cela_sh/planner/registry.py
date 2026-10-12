"""Registration point for local planning model providers.

Extending planning providers is additive. Implement
:class:`~cela_sh.planner.base.Planner` in a new module, then call
:func:`register_planner_provider` with a builder that receives the runtime
settings, the ``(connect, read)`` timeout pair, the optional
:class:`~cela_sh.memory.base.MemoryStore`, and any CLI model override. No
existing module, setting name, or call site has to be edited, and every
provider is held to the same contract by ``tests/contract_planner.py``.

The registry is the single source of truth for the accepted
``CELA_PLANNER_PROVIDER`` values: :mod:`cela_sh.settings` validates against
:func:`planner_provider_names`, and :func:`cela_sh.factory.create_planner`
builds through :func:`build_planner`.
"""

from collections.abc import Callable
from typing import Protocol

from ..memory import MemoryStore
from .base import Planner

DEFAULT_PLANNER_PROVIDER = "ollama"

#: Local model providers this repository ships. Names are data only, so settings
#: validation never has to import an adapter (or its HTTP client).
BUILTIN_PLANNER_PROVIDERS = frozenset({DEFAULT_PLANNER_PROVIDER})


class PlannerProviderSettings(Protocol):
    """The settings fields a planner provider builder is allowed to read."""

    allow_ollama_container_host: bool
    memory_limit: int
    ollama_url: str
    planner_model: str


PlannerProviderBuilder = Callable[
    [PlannerProviderSettings, tuple[float, float], MemoryStore | None, str | None],
    Planner,
]

_BUILDERS: dict[str, PlannerProviderBuilder] = {}


def _normalise(name: str) -> str:
    if not isinstance(name, str) or not name.strip():
        raise ValueError("A planner provider name must be a non-blank string.")
    return name.strip().lower()


def register_planner_provider(
    name: str,
    builder: PlannerProviderBuilder,
    *,
    replace: bool = False,
) -> None:
    """Register ``builder`` as the factory for the local planner provider called ``name``.

    Names are compared case-insensitively. Registering the same builder twice is
    a no-op; registering a different builder over an existing name requires
    ``replace=True`` so an accidental collision fails loudly instead of quietly
    changing which planner a deployment gets.
    """
    key = _normalise(name)
    if not callable(builder):
        raise TypeError("A planner provider builder must be callable.")
    current = _BUILDERS.get(key)
    if current is not None and current is not builder and not replace:
        raise ValueError(f"Planner provider {key!r} is already registered; pass replace=True to override it.")
    _BUILDERS[key] = builder


def unregister_planner_provider(name: str) -> bool:
    """Remove a planner provider registration. Returns True when something was removed.

    Intended for tests and for extensions that are torn down at runtime.
    Built-in names stay accepted by settings validation either way.
    """
    return _BUILDERS.pop(_normalise(name), None) is not None


def planner_provider_names() -> tuple[str, ...]:
    """Every value ``CELA_PLANNER_PROVIDER`` accepts, sorted and de-duplicated."""
    return tuple(sorted(BUILTIN_PLANNER_PROVIDERS | frozenset(_BUILDERS)))


def registered_planner_providers() -> tuple[str, ...]:
    """Names that have a registered builder."""
    return tuple(sorted(_BUILDERS))


def is_known_planner_provider(name: object) -> bool:
    """True when ``name`` is an accepted ``CELA_PLANNER_PROVIDER`` value."""
    return isinstance(name, str) and name.strip().lower() in planner_provider_names()


def satisfies_planner_contract(candidate: object) -> bool:
    """True when ``candidate`` exposes the :class:`Planner` operation.

    The check is structural rather than ``isinstance``-based on purpose: an
    extension may subclass ``Planner`` or simply implement its contract, and
    test doubles keep working. It still rejects ``None``, plain data, and
    half-finished adapters at start-up instead of during planning.
    """
    if isinstance(candidate, Planner):
        return True
    return callable(getattr(candidate, "generate_plan", None))


def build_planner(
    name: str,
    settings: PlannerProviderSettings,
    timeout: tuple[float, float],
    memory_store: MemoryStore | None = None,
    model: str | None = None,
) -> Planner:
    """Build the planner for a registered local model provider.

    Raises ``ConfigurationError`` for an unknown name and ``TypeError`` for a
    builder that does not return a :class:`Planner`, so a broken extension
    fails at start-up instead of degrading planning silently.
    """
    from ..settings import ConfigurationError  # imported late: settings validates through this module

    key = _normalise(name)
    builder = _BUILDERS.get(key)
    if builder is None and key in BUILTIN_PLANNER_PROVIDERS:
        import cela_sh.factory  # imported late: it registers the built-in builders on first use

        builder = _BUILDERS.get(key)
    if builder is None:
        raise ConfigurationError(
            f"CELA_PLANNER_PROVIDER {key!r} has no registered builder. Known providers: "
            f"{', '.join(planner_provider_names())}."
        )

    planner = builder(settings, timeout, memory_store, model)
    if not satisfies_planner_contract(planner):
        raise TypeError(
            f"Planner provider {key!r} must build an object implementing the Planner contract "
            "(generate_plan)."
        )
    return planner
