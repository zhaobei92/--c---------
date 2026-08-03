from pydantic import BaseModel, ConfigDict, Field


class CommitRequest(BaseModel):
    """方案 11.6：提交决定。accepted_tradeoffs 必须为 true 才能锁定。"""

    selected_option_id: str
    accepted_tradeoffs: bool


class ClosureContractOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    selected_option_id: str
    accepted_tradeoffs: list[str]
    main_reasons: list[str]
    reopen_conditions: list[str]
    non_reopen_conditions: list[str]
    next_action: str
    followed_recommendation: bool
    user_confirmed: bool


class ReopenCreateRequest(BaseModel):
    """方案 11.7：请求重开。"""

    new_information: str = Field(min_length=1, max_length=4000)
    evidence_ids: list[str] = []


class ReopenResponse(BaseModel):
    outcome: str  # full_reopen | new_facts_only | rejected | closure_intervention
    reopen_score: float
    novelty: float
    credibility: float
    relevance: float
    flip_probability: float
    is_rumination: bool
    message: str
    status: str
