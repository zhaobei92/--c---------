from pydantic import BaseModel, ConfigDict


class RecommendationExplanation(BaseModel):
    """推荐解释契约（方案 12.4）。

    模型只能解释算法结果，recommended_option_id 必须与算法输出一致，
    否则整个模型输出会被丢弃。
    """

    recommended_option_id: str
    summary: str
    main_reasons: list[str]
    accepted_tradeoffs: list[str]
    critical_unknowns: list[str]
    reopen_conditions: list[str]
    non_reopen_conditions: list[str]
    next_action: str


class ChallengeResult(BaseModel):
    """Challenger 输出契约（方案 9.7）：只找问题，不产生新推荐。"""

    missing_assumptions: list[str] = []
    possible_biases: list[str] = []
    constraint_violations: list[str] = []
    fragile_variables: list[str] = []
    counterargument: str = ""
    requires_recompute: bool = False


class RecommendationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    decision_run_id: str
    recommended_option_id: str
    summary: str
    main_reasons: list[str]
    accepted_tradeoffs: list[str]
    critical_unknowns: list[str]
    reopen_conditions: list[str]
    non_reopen_conditions: list[str]
    next_action: str
    challenger_output: dict | None = None
    source: str
