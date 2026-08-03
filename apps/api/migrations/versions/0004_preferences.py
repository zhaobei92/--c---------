"""phase 3: decision_criteria and pairwise_comparisons

Revision ID: 0004
Revises: 0003
Create Date: 2026-08-04

"""
from alembic import op
import sqlalchemy as sa

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "decision_criteria",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "decision_case_id",
            sa.String(36),
            sa.ForeignKey("decision_cases.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("criterion_type", sa.String(20), nullable=False, server_default="compensatory"),
        sa.Column("direction", sa.String(20), nullable=False, server_default="higher_better"),
        sa.Column("utility_curve_type", sa.String(20), nullable=False, server_default="linear"),
        sa.Column("minimum_acceptable", sa.Float(), nullable=True),
        sa.Column("veto_threshold", sa.Float(), nullable=True),
        sa.Column("initial_weight", sa.Float(), nullable=False, server_default="0.2"),
        sa.Column("learned_weight", sa.Float(), nullable=True),
        sa.Column("weight_uncertainty", sa.Float(), nullable=True),
        sa.Column("source", sa.String(40), nullable=False, server_default="ai_generated"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "pairwise_comparisons",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "decision_case_id",
            sa.String(36),
            sa.ForeignKey("decision_cases.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "left_criterion_id",
            sa.String(36),
            sa.ForeignKey("decision_criteria.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "right_criterion_id",
            sa.String(36),
            sa.ForeignKey("decision_criteria.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("choice", sa.String(20), nullable=False),
        sa.Column("strength", sa.Float(), nullable=False, server_default="0.5"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("pairwise_comparisons")
    op.drop_table("decision_criteria")
