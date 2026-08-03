from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

Checkpoint = Literal["h24", "d7", "d30", "d90"]


class FollowupCreateRequest(BaseModel):
    checkpoint: Checkpoint
    executed: bool | None = None
    satisfaction: float | None = Field(default=None, ge=0, le=1)
    regret_level: float | None = Field(default=None, ge=0, le=1)
    worried_risk_occurred: bool | None = None
    notes: str | None = Field(default=None, max_length=2000)


class FollowupOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    checkpoint: str
    executed: bool | None
    satisfaction: float | None
    regret_level: float | None
    worried_risk_occurred: bool | None
    notes: str | None


class DueFollowupsResponse(BaseModel):
    due: list[str]


class PreferencePosteriorOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    criterion_name: str
    category: str
    posterior_mean: float
    posterior_std: float
    evidence_count: int
