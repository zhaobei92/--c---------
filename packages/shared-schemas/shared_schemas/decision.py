from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from shared_schemas.enums import (
    DecisionDomain,
    DecisionStatus,
    InputType,
    MessageRole,
    RiskLevel,
)


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


class DecisionCaseDetail(DecisionCaseSummary):
    primary_stuck_type: str | None = None
    information_completeness: float | None = None
    decision_readiness: float | None = None
    selected_option_id: str | None = None
    committed_at: datetime | None = None
    options: list[DecisionOptionOut] = []
    messages: list[DecisionMessageOut] = []


class MessageCreateRequest(BaseModel):
    content: str = Field(min_length=1, max_length=8000)


class MessageCreateResponse(BaseModel):
    user_message: DecisionMessageOut
    assistant_message: DecisionMessageOut
    status: DecisionStatus
