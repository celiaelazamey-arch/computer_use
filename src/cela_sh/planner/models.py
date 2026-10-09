from enum import Enum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class Action(str, Enum):
    OPEN_APP = "OPEN_APP"
    SEARCH_WEB = "SEARCH_WEB"
    NAVIGATE = "NAVIGATE"
    OBSERVE = "OBSERVE"
    CLICK = "CLICK"
    TYPE = "TYPE"
    SCROLL = "SCROLL"
    WAIT = "WAIT"
    ASK_USER = "ASK_USER"
    STOP = "STOP"


class RiskLevel(str, Enum):
    LOW = "low"
    REQUIRES_CONFIRMATION = "requires_confirmation"
    BLOCKED = "blocked"


class PlanStatus(str, Enum):
    READY = "ready"
    REQUIRES_CONFIRMATION = "requires_confirmation"
    BLOCKED = "blocked"


class PlannerInput(StrictModel):
    goal: str = Field(min_length=1, max_length=2_000)
    screen_context: str = Field(default="", max_length=8_000)

    @model_validator(mode="after")
    def goal_is_not_blank(self):
        if not self.goal.strip():
            raise ValueError("goal must contain non-whitespace text")
        return self


class RawStep(StrictModel):
    action: Action = Field(description="One action from the allowed action enum.")
    target: str = Field(
        max_length=500,
        description="The app, destination, or UI element acted on; not the search terms or text payload.",
    )
    value: str = Field(
        max_length=2_000,
        description=(
            "Action payload: search query for SEARCH_WEB, URL for NAVIGATE, exact text for TYPE, "
            "or empty when the action needs no payload."
        ),
    )
    reason: str = Field(min_length=1, max_length=500, description="Brief user-facing purpose for this step.")

    @model_validator(mode="after")
    def search_requires_query(self):
        if self.action is Action.SEARCH_WEB and not (self.target.strip() or self.value.strip()):
            raise ValueError("SEARCH_WEB must contain a search query")
        return self


class RawPlan(StrictModel):
    analysis: str = Field(min_length=1, max_length=1_500)
    steps: list[RawStep] = Field(min_length=1, max_length=20)

    @model_validator(mode="after")
    def stop_must_be_last(self):
        stop_indexes = [index for index, step in enumerate(self.steps) if step.action is Action.STOP]
        if len(stop_indexes) > 1 or (stop_indexes and stop_indexes[0] != len(self.steps) - 1):
            raise ValueError("STOP may appear once and only as the last step")
        return self


class PlannedStep(RawStep):
    risk: RiskLevel
    requires_confirmation: bool
    policy_reasons: list[str]


class Plan(StrictModel):
    analysis: str
    steps: list[PlannedStep]
    status: PlanStatus
    requires_confirmation: bool
    dry_run: Literal[True] = True
    execution_enabled: Literal[False] = False
