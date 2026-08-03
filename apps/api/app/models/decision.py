from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, String, Text
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
