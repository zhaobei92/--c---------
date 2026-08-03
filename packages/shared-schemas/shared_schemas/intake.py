from pydantic import BaseModel, Field

from shared_schemas.enums import DecisionDomain


class ExtractedOption(BaseModel):
    name: str
    description: str | None = None


class ExtractedConstraint(BaseModel):
    description: str
    is_hard: bool = False


class IntakeExtraction(BaseModel):
    """Intake Parser 的结构化输出契约（阶段1定义，阶段2接入 LLM）。"""

    title: str = Field(max_length=120)
    domain: DecisionDomain
    options: list[ExtractedOption]
    facts: list[str]
    constraints: list[ExtractedConstraint]
    concerns: list[str]
    unknowns: list[str]
    clarification_required: bool
