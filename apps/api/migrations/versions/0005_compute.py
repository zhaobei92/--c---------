"""phase 4: option_evaluations and decision_runs

Revision ID: 0005
Revises: 0004
Create Date: 2026-08-04

"""
from alembic import op
import sqlalchemy as sa

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "option_evaluations",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "decision_case_id",
            sa.String(36),
            sa.ForeignKey("decision_cases.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "option_id",
            sa.String(36),
            sa.ForeignKey("decision_options.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "criterion_id",
            sa.String(36),
            sa.ForeignKey("decision_criteria.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("expected_value", sa.Float(), nullable=False),
        sa.Column("uncertainty", sa.Float(), nullable=False, server_default="0.15"),
        sa.Column("distribution", sa.String(20), nullable=False, server_default="normal"),
        sa.Column("source", sa.String(40), nullable=False, server_default="user_rating"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("option_id", "criterion_id", name="uq_evaluation_option_criterion"),
    )
    op.create_table(
        "decision_runs",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "decision_case_id",
            sa.String(36),
            sa.ForeignKey("decision_cases.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("algorithm_version", sa.String(40), nullable=False),
        sa.Column("seed", sa.Integer(), nullable=False, server_default="42"),
        sa.Column("input_snapshot", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("result_snapshot", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("ranking_stability", sa.Float(), nullable=True),
        sa.Column("winning_option_id", sa.String(36), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("decision_runs")
    op.drop_table("option_evaluations")
