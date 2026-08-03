"""intake facets, hard_constraints, audit_events, model_invocations

Revision ID: 0002
Revises: 0001
Create Date: 2026-08-04

"""
from alembic import op
import sqlalchemy as sa

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "decision_cases",
        sa.Column("facts", sa.JSON(), nullable=False, server_default="[]"),
    )
    op.add_column(
        "decision_cases",
        sa.Column("concerns", sa.JSON(), nullable=False, server_default="[]"),
    )
    op.add_column(
        "decision_cases",
        sa.Column("unknowns", sa.JSON(), nullable=False, server_default="[]"),
    )

    op.create_table(
        "hard_constraints",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "decision_case_id",
            sa.String(36),
            sa.ForeignKey("decision_cases.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("is_hard", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("source", sa.String(40), nullable=False, server_default="user_input"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )

    op.create_table(
        "audit_events",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "decision_case_id",
            sa.String(36),
            sa.ForeignKey("decision_cases.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("user_id", sa.String(36), nullable=True),
        sa.Column("event_type", sa.String(60), nullable=False, index=True),
        sa.Column("payload", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )

    op.create_table(
        "model_invocations",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "decision_case_id",
            sa.String(36),
            sa.ForeignKey("decision_cases.id", ondelete="SET NULL"),
            nullable=True,
            index=True,
        ),
        sa.Column("task_kind", sa.String(60), nullable=False),
        sa.Column("model_role", sa.String(20), nullable=False),
        sa.Column("model_name", sa.String(120), nullable=True),
        sa.Column("success", sa.Boolean(), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("latency_ms", sa.Float(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("model_invocations")
    op.drop_table("audit_events")
    op.drop_table("hard_constraints")
    op.drop_column("decision_cases", "unknowns")
    op.drop_column("decision_cases", "concerns")
    op.drop_column("decision_cases", "facts")
