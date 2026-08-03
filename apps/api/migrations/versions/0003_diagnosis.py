"""phase 2: stuck-type diagnosis fields and clarification tracking

Revision ID: 0003
Revises: 0002
Create Date: 2026-08-04

"""
from alembic import op
import sqlalchemy as sa

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "decision_cases",
        sa.Column("stuck_type_scores", sa.JSON(), nullable=False, server_default="{}"),
    )
    op.add_column(
        "decision_cases",
        sa.Column("asked_questions", sa.JSON(), nullable=False, server_default="[]"),
    )
    op.add_column(
        "decision_cases",
        sa.Column("clarification_rounds", sa.Integer(), nullable=False, server_default="0"),
    )


def downgrade() -> None:
    op.drop_column("decision_cases", "clarification_rounds")
    op.drop_column("decision_cases", "asked_questions")
    op.drop_column("decision_cases", "stuck_type_scores")
