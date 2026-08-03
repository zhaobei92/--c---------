"""phase 7: followup_outcomes and user_preference_posteriors

Revision ID: 0008
Revises: 0007
Create Date: 2026-08-04

"""
from alembic import op
import sqlalchemy as sa

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "followup_outcomes",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "decision_case_id",
            sa.String(36),
            sa.ForeignKey("decision_cases.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("checkpoint", sa.String(10), nullable=False),
        sa.Column("executed", sa.Boolean(), nullable=True),
        sa.Column("satisfaction", sa.Float(), nullable=True),
        sa.Column("regret_level", sa.Float(), nullable=True),
        sa.Column("worried_risk_occurred", sa.Boolean(), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("decision_case_id", "checkpoint", name="uq_followup_checkpoint"),
    )
    op.create_table(
        "user_preference_posteriors",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "user_id",
            sa.String(36),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("criterion_name", sa.String(120), nullable=False),
        sa.Column("category", sa.String(40), nullable=False),
        sa.Column("posterior_mean", sa.Float(), nullable=False),
        sa.Column("posterior_std", sa.Float(), nullable=False),
        sa.Column("evidence_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "user_id", "criterion_name", "category", name="uq_posterior_user_criterion"
        ),
    )


def downgrade() -> None:
    op.drop_table("user_preference_posteriors")
    op.drop_table("followup_outcomes")
