from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, Float, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, UUIDPrimaryKeyMixin, utcnow


class AuditEvent(Base, UUIDPrimaryKeyMixin):
    """状态转换、AI 提取结果应用、用户修改等全部落审计日志。"""

    __tablename__ = "audit_events"

    decision_case_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("decision_cases.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    user_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    event_type: Mapped[str] = mapped_column(String(60), nullable=False, index=True)
    payload: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )


class ModelInvocation(Base, UUIDPrimaryKeyMixin):
    """每次大模型调用的成本 / 延迟 / 成败记录。不保存隐藏思维过程。"""

    __tablename__ = "model_invocations"

    decision_case_id: Mapped[str | None] = mapped_column(
        String(36),
        ForeignKey("decision_cases.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    task_kind: Mapped[str] = mapped_column(String(60), nullable=False)
    model_role: Mapped[str] = mapped_column(String(20), nullable=False)
    model_name: Mapped[str | None] = mapped_column(String(120), nullable=True)
    success: Mapped[bool] = mapped_column(Boolean, nullable=False)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    latency_ms: Mapped[float | None] = mapped_column(Float, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
