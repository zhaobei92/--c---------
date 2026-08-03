from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from shared_schemas.enums import (
    DecisionDomain,
    DecisionStatus,
    InputType,
    MessageRole,
    RiskLevel,
)
from shared_schemas.preference import CriterionOut


class DecisionCreateRequest(BaseModel):
    input: str = Field(min_length=1, max_length=8000)
    input_type: InputType = InputType.TEXT


class DecisionCreateResponse(BaseModel):
    decision_id: str
    status: DecisionStatus
    stream_url: str


class DecisionOptionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    description: str | None = None
    is_eligible: bool
    elimination_reason: str | None = None
    source: str


class DecisionMessageOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    role: MessageRole
    content: str
    created_at: datetime


class DecisionCaseSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    title: str
    domain: DecisionDomain
    risk_level: RiskLevel
    status: DecisionStatus
    created_at: datetime
    updated_at: datetime


class HardConstraintOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    description: str
    is_hard: bool
    source: str


class DecisionCaseDetail(DecisionCaseSummary):
    primary_stuck_type: str | None = None
    information_completeness: float | None = None
    decision_readiness: float | None = None
    selected_option_id: str | None = None
    committed_at: datetime | None = None
    facts: list[str] = []
    concerns: list[str] = []
    unknowns: list[str] = []
    options: list[DecisionOptionOut] = []
    constraints: list[HardConstraintOut] = []
    criteria: list[CriterionOut] = []
    messages: list[DecisionMessageOut] = []


class MessageCreateRequest(BaseModel):
    content: str = Field(min_length=1, max_length=8000)


class OptionCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    description: str | None = None


class OptionUpdateRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = None
    is_eligible: bool | None = None
    elimination_reason: str | None = None


class ConstraintCreateRequest(BaseModel):
    description: str = Field(min_length=1, max_length=2000)
    is_hard: bool = True


class ConstraintUpdateRequest(BaseModel):
    description: str | None = Field(default=None, min_length=1, max_length=2000)
    is_hard: bool | None = None


class MessageCreateResponse(BaseModel):
    user_message: DecisionMessageOut
    assistant_message: DecisionMessageOut
    status: DecisionStatus
