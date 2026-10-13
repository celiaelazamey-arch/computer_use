from abc import ABC, abstractmethod
import math
from typing import Any

from pydantic import ValidationError

from ..memory import MemoryStore, MemoryStoreError
from ..memory.metadata import correction_first, normalize_memory_type, normalize_tags
from ..memory.scopes import GLOBAL_MEMORY_SESSION_ID, project_session_id
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
    ) -> tuple[PlannerInput, list[dict[str, Any]]]:
        """Validate planner input and retrieve project plus global memories."""
        from .client import PlannerMemoryError, PlannerValidationError

        try:
            request = PlannerInput(goal=goal, screen_context=screen_context)
        except ValidationError as exc:
            raise PlannerValidationError("Planner input did not meet the required schema.") from exc

        memory_context: list[dict[str, Any]] = []
        if self.memory_store is not None:
            try:
                project_session = project_session_id(session_id)
            except ValueError as exc:
                raise PlannerValidationError("A valid project session_id is required when memory is enabled.") from exc

            # Retrieve more candidates than the prompt limit so corrections
            # discovered just below the normal top-k can receive their explicit
            # priority. Hard-cap each scope query to bound local and Qdrant I/O.
            candidate_limit = min(max(self.memory_limit * 5, self.memory_limit), 100)
            records: list[dict[str, Any]] = []
            for scope, memory_session in (
                ("project", project_session),
                ("global", GLOBAL_MEMORY_SESSION_ID),
            ):
                try:
                    memories = self.memory_store.search_memory(
                        query=request.goal,
                        session_id=memory_session,
                        limit=candidate_limit,
                    )
                except MemoryStoreError as exc:
                    raise PlannerMemoryError("Memory lookup failed.") from exc

                if not isinstance(memories, list):
                    raise PlannerMemoryError("Memory store returned invalid results.")
                for memory in memories:
                    if (
                        not isinstance(memory, dict)
                        or memory.get("session_id") != memory_session
                        or not isinstance(memory.get("content"), str)
                        or not memory["content"].strip()
                        or len(memory["content"]) > 8_000
                        or not isinstance(memory.get("metadata"), dict)
                        or isinstance(memory.get("score"), bool)
                        or not isinstance(memory.get("score"), (int, float))
                        or not math.isfinite(memory["score"])
                    ):
                        raise PlannerMemoryError("Memory store returned invalid results.")
                    metadata = memory["metadata"]
                    try:
                        memory_type = normalize_memory_type(metadata.get("type"))
                        tags = normalize_tags(metadata.get("tags", []))
                    except ValueError:
                        memory_type = "note"
                        tags = []
                    records.append(
                        {
                            "session_id": memory_session,
                            "content": memory["content"],
                            "metadata": {**metadata, "type": memory_type, "tags": tags},
                            "score": float(memory["score"]),
                            "scope": scope,
                        }
                    )

            prioritized = correction_first(records)[: self.memory_limit]
            memory_context = [
                {
                    "content": memory["content"],
                    "type": memory["metadata"]["type"],
                    "tags": memory["metadata"]["tags"],
                    "scope": memory["scope"],
                }
                for memory in prioritized
            ]

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
