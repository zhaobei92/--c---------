from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class CriterionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    criterion_type: str
    direction: str
    utility_curve_type: str
    minimum_acceptable: float | None = None
    veto_threshold: float | None = None
    initial_weight: float
    learned_weight: float | None = None
    weight_uncertainty: float | None = None
    source: str


class GeneratedCriterion(BaseModel):
    name: str = Field(max_length=60)
    utility_curve_type: Literal[
        "linear", "diminishing", "threshold", "s_curve", "loss_averse", "categorical"
    ] = "linear"
    rationale: str = ""


class CriteriaGeneration(BaseModel):
    """标准生成的 LLM 输出契约：只提出标准，禁止携带权重。"""

    criteria: list[GeneratedCriterion] = Field(max_length=7)


class ComparisonCreateRequest(BaseModel):
    left_criterion_id: str
    right_criterion_id: str
    choice: Literal["left", "right", "equal", "incomparable"]
    strength: float = Field(default=0.5, ge=0, le=1)


class ComparisonState(BaseModel):
    comparisons_done: int
    comparisons_required: int
    consistency: float
    criteria: list[CriterionOut]


class NextComparisonResponse(BaseModel):
    done: bool
    left: CriterionOut | None = None
    right: CriterionOut | None = None
    prompt: str | None = None
    comparisons_done: int
    comparisons_required: int
