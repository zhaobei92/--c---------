"""phase 5: recommendations table

Revision ID: 0006
Revises: 0005
Create Date: 2026-08-04

"""
from alembic import op
import sqlalchemy as sa

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "recommendations",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "decision_case_id",
            sa.String(36),
            sa.ForeignKey("decision_cases.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "decision_run_id",
            sa.String(36),
            sa.ForeignKey("decision_runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("recommended_option_id", sa.String(36), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("main_reasons", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("accepted_tradeoffs", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("critical_unknowns", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("reopen_conditions", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("non_reopen_conditions", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("next_action", sa.Text(), nullable=False, server_default=""),
        sa.Column("challenger_output", sa.JSON(), nullable=True),
        sa.Column("source", sa.String(20), nullable=False, server_default="deterministic"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("recommendations")
