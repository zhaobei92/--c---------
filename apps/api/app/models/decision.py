from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, Float, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from shared_schemas import DecisionDomain, DecisionStatus, MessageRole, RiskLevel


class DecisionCase(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "decision_cases"

    user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    domain: Mapped[str] = mapped_column(
        String(40), default=DecisionDomain.OTHER, nullable=False
    )
    risk_level: Mapped[str] = mapped_column(
        String(20), default=RiskLevel.LOW, nullable=False
    )
    status: Mapped[str] = mapped_column(
        String(40), default=DecisionStatus.DRAFT, nullable=False, index=True
    )
    primary_stuck_type: Mapped[str | None] = mapped_column(String(40), nullable=True)
    information_completeness: Mapped[float | None] = mapped_column(Float, nullable=True)
    decision_readiness: Mapped[float | None] = mapped_column(Float, nullable=True)
    selected_option_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    committed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # Intake 提取的非选项信息；阶段5起 facts 迁入 evidence_items 表
    facts: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    concerns: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    unknowns: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    # 阶段2：诊断分数分布与追问记录
    stuck_type_scores: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    asked_questions: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    clarification_rounds: Mapped[int] = mapped_column(default=0, nullable=False)

    options: Mapped[list["DecisionOption"]] = relationship(
        back_populates="decision_case",
        cascade="all, delete-orphan",
        order_by="DecisionOption.created_at",
    )
    messages: Mapped[list["DecisionMessage"]] = relationship(
        back_populates="decision_case",
        cascade="all, delete-orphan",
        order_by="DecisionMessage.created_at",
    )
    constraints: Mapped[list["HardConstraint"]] = relationship(
        back_populates="decision_case",
        cascade="all, delete-orphan",
        order_by="HardConstraint.created_at",
    )
    criteria: Mapped[list["DecisionCriterion"]] = relationship(
        back_populates="decision_case",
        cascade="all, delete-orphan",
        order_by="DecisionCriterion.created_at",
    )
    comparisons: Mapped[list["PairwiseComparison"]] = relationship(
        back_populates="decision_case",
        cascade="all, delete-orphan",
        order_by="PairwiseComparison.created_at",
    )
    evaluations: Mapped[list["OptionEvaluation"]] = relationship(
        back_populates="decision_case",
        cascade="all, delete-orphan",
        order_by="OptionEvaluation.created_at",
    )
    runs: Mapped[list["DecisionRun"]] = relationship(
        back_populates="decision_case",
        cascade="all, delete-orphan",
        order_by="DecisionRun.created_at",
    )


class DecisionOption(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "decision_options"

    decision_case_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("decision_cases.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_eligible: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    elimination_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    source: Mapped[str] = mapped_column(String(40), default="user_input", nullable=False)

    decision_case: Mapped[DecisionCase] = relationship(back_populates="options")


class DecisionMessage(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "decision_messages"

    decision_case_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("decision_cases.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    role: Mapped[str] = mapped_column(String(20), default=MessageRole.USER, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)

    decision_case: Mapped[DecisionCase] = relationship(back_populates="messages")


class HardConstraint(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """约束条件。is_hard=True 的为不可补偿硬约束，参与阶段4的直接淘汰。"""

    __tablename__ = "hard_constraints"

    decision_case_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("decision_cases.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    description: Mapped[str] = mapped_column(Text, nullable=False)
    is_hard: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    source: Mapped[str] = mapped_column(String(40), default="user_input", nullable=False)

    decision_case: Mapped[DecisionCase] = relationship(back_populates="constraints")


class DecisionCriterion(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """决策标准。权重只来自用户成对比较（learned_weight），初始权重仅为兜底。"""

    __tablename__ = "decision_criteria"

    decision_case_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("decision_cases.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    criterion_type: Mapped[str] = mapped_column(
        String(20), default="compensatory", nullable=False
    )  # compensatory | veto | threshold
    direction: Mapped[str] = mapped_column(
        String(20), default="higher_better", nullable=False
    )
    utility_curve_type: Mapped[str] = mapped_column(
        String(20), default="linear", nullable=False
    )
    minimum_acceptable: Mapped[float | None] = mapped_column(Float, nullable=True)
    veto_threshold: Mapped[float | None] = mapped_column(Float, nullable=True)
    initial_weight: Mapped[float] = mapped_column(Float, default=0.2, nullable=False)
    learned_weight: Mapped[float | None] = mapped_column(Float, nullable=True)
    weight_uncertainty: Mapped[float | None] = mapped_column(Float, nullable=True)
    source: Mapped[str] = mapped_column(String(40), default="ai_generated", nullable=False)

    decision_case: Mapped[DecisionCase] = relationship(back_populates="criteria")


class PairwiseComparison(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "pairwise_comparisons"

    decision_case_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("decision_cases.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    left_criterion_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("decision_criteria.id", ondelete="CASCADE"), nullable=False
    )
    right_criterion_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("decision_criteria.id", ondelete="CASCADE"), nullable=False
    )
    choice: Mapped[str] = mapped_column(String(20), nullable=False)
    strength: Mapped[float] = mapped_column(Float, default=0.5, nullable=False)

    decision_case: Mapped[DecisionCase] = relationship(back_populates="comparisons")


class OptionEvaluation(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """选项在某标准上的评估。以期望值+不确定度保存，不保存伪精确单值。"""

    __tablename__ = "option_evaluations"

    decision_case_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("decision_cases.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    option_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("decision_options.id", ondelete="CASCADE"), nullable=False
    )
    criterion_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("decision_criteria.id", ondelete="CASCADE"), nullable=False
    )
    expected_value: Mapped[float] = mapped_column(Float, nullable=False)  # 0-1
    uncertainty: Mapped[float] = mapped_column(Float, default=0.15, nullable=False)
    distribution: Mapped[str] = mapped_column(String(20), default="normal", nullable=False)
    source: Mapped[str] = mapped_column(String(40), default="user_rating", nullable=False)

    decision_case: Mapped[DecisionCase] = relationship(back_populates="evaluations")


class DecisionRun(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """每次决策计算的版本化快照：算法升级后仍能解释历史结论。"""

    __tablename__ = "decision_runs"

    decision_case_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("decision_cases.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    algorithm_version: Mapped[str] = mapped_column(String(40), nullable=False)
    seed: Mapped[int] = mapped_column(default=42, nullable=False)
    input_snapshot: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    result_snapshot: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    ranking_stability: Mapped[float | None] = mapped_column(Float, nullable=True)
    winning_option_id: Mapped[str | None] = mapped_column(String(36), nullable=True)

    decision_case: Mapped[DecisionCase] = relationship(back_populates="runs")
