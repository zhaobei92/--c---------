from pydantic import BaseModel, ConfigDict, Field


class EvaluationItem(BaseModel):
    option_id: str
    criterion_id: str
    expected_value: float = Field(ge=0, le=1)
    uncertainty: float = Field(default=0.15, ge=0.01, le=0.5)


class EvaluationUpsertRequest(BaseModel):
    evaluations: list[EvaluationItem] = Field(min_length=1, max_length=200)


class EvaluationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    option_id: str
    criterion_id: str
    expected_value: float
    uncertainty: float
    distribution: str
    source: str


class DecisionRunOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    algorithm_version: str
    seed: int
    ranking_stability: float | None
    winning_option_id: str | None
    result_snapshot: dict
