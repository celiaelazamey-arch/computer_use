from abc import ABC, abstractmethod
from typing import Any

from pydantic import ValidationError

from ..memory import MemoryStore, MemoryStoreError
from .models import Plan, PlannerInput, RawPlan
from .policy import apply_policy


class Planner(ABC):
    """Contract shared by local, dry-run-only planning model providers."""

    memory_store: MemoryStore | None = None
    memory_limit: int = 3

    @abstractmethod
    def generate_plan(
        self,
        goal: str,
        screen_context: str = "",
        session_id: str | None = None,
    ) -> Plan:
        """Return a validated, policy-classified dry-run plan for ``goal``."""
        raise NotImplementedError

    def _prepare_request(
        self,
        goal: str,
        screen_context: str = "",
        session_id: str | None = None,
    ) -> tuple[PlannerInput, list[dict[str, str]]]:
        """Validate planner input and retrieve session-scoped memories when enabled."""
        from .client import PlannerMemoryError, PlannerValidationError

        try:
            request = PlannerInput(goal=goal, screen_context=screen_context)
        except ValidationError as exc:
            raise PlannerValidationError("Planner input did not meet the required schema.") from exc

        memory_context: list[dict[str, str]] = []
        if self.memory_store is not None:
            if session_id is None or not session_id.strip():
                raise PlannerValidationError("A non-blank session_id is required when memory is enabled.")
            try:
                memories = self.memory_store.search_memory(
                    query=request.goal,
                    session_id=session_id.strip(),
                    limit=self.memory_limit,
                )
            except MemoryStoreError as exc:
                raise PlannerMemoryError("Memory lookup failed.") from exc

            if not isinstance(memories, list) or any(
                not isinstance(memory, dict)
                or not isinstance(memory.get("content"), str)
                or len(memory["content"]) > 8_000
                for memory in memories
            ):
                raise PlannerMemoryError("Memory store returned invalid results.")
            memory_context = [{"content": memory["content"]} for memory in memories]

        return request, memory_context

    def _finalize_plan(
        self,
        raw_response: str | dict[str, Any],
        request: PlannerInput,
    ) -> Plan:
        """Validate raw model output against the strict schema and apply deterministic policy."""
        from .client import PlannerValidationError

        try:
            if isinstance(raw_response, str):
                raw_plan = RawPlan.model_validate_json(raw_response)
            elif isinstance(raw_response, dict):
                raw_plan = RawPlan.model_validate(raw_response)
            else:
                raise PlannerValidationError("Generated plan did not match the strict schema.")
        except ValidationError as exc:
            raise PlannerValidationError("Generated plan did not match the strict schema.") from exc

        return apply_policy(raw_plan, request)
