from typing import Literal

from pydantic import BaseModel, Field

from shared_schemas.enums import RiskLevel, StuckType


class RiskAssessment(BaseModel):
    """LLM 风险精细化输出。系统只允许用它调高等级，永不调低。"""

    risk_level: RiskLevel
    rationale: str = ""


class StuckTypeDiagnosis(BaseModel):
    """纠结类型诊断。

    注意：information_completeness / decision_readiness 由系统确定性计算，
    模型输出的这两个字段只作参考，不得作为系统置信度。
    """

    primary_type: StuckType
    secondary_types: list[StuckType] = []
    type_scores: dict[StuckType, float] = {}
    information_completeness: float = Field(ge=0, le=1)
    decision_readiness: float = Field(ge=0, le=1)
    explanation_summary: str = ""


class NextQuestion(BaseModel):
    question: str
    target_variable: str
    expected_value: float = Field(ge=0, le=1)
    answer_type: Literal["single_choice", "pairwise", "number", "free_text"] = "free_text"
    choices: list[str] | None = None
    stop_if_answered: bool = False


class AdvanceResponse(BaseModel):
    """Orchestrator 单步推进的结果：告诉前端下一步该展示什么。"""

    kind: Literal[
        "diagnosis",
        "question",
        "comparison_needed",
        "evaluations_needed",
        "ready_to_compute",
        "recommendation_ready",
        "guided_only",
        "noop",
    ]
    status: str
    message: str | None = None
    diagnosis: StuckTypeDiagnosis | None = None
    question: NextQuestion | None = None
