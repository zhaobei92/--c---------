"""phase 6: closure_contracts and reopen_requests

Revision ID: 0007
Revises: 0006
Create Date: 2026-08-04

"""
from alembic import op
import sqlalchemy as sa

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "closure_contracts",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "decision_case_id",
            sa.String(36),
            sa.ForeignKey("decision_cases.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("selected_option_id", sa.String(36), nullable=False),
        sa.Column("accepted_tradeoffs", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("main_reasons", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("reopen_conditions", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("non_reopen_conditions", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("next_action", sa.Text(), nullable=False, server_default=""),
        sa.Column("followed_recommendation", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("user_confirmed", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "reopen_requests",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "decision_case_id",
            sa.String(36),
            sa.ForeignKey("decision_cases.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("new_information", sa.Text(), nullable=False),
        sa.Column("novelty", sa.Float(), nullable=False),
        sa.Column("credibility", sa.Float(), nullable=False),
        sa.Column("relevance", sa.Float(), nullable=False),
        sa.Column("flip_probability", sa.Float(), nullable=False),
        sa.Column("info_value", sa.Float(), nullable=False),
        sa.Column("reopen_score", sa.Float(), nullable=False),
        sa.Column("outcome", sa.String(30), nullable=False),
        sa.Column("is_rumination", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("reopen_requests")
    op.drop_table("closure_contracts")
