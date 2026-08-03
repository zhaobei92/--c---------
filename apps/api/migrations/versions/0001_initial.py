"""initial tables: users, decision_cases, decision_options, decision_messages

Revision ID: 0001
Revises:
Create Date: 2026-08-04

"""
from alembic import op
import sqlalchemy as sa

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("email", sa.String(320), nullable=True, unique=True),
        sa.Column("display_name", sa.String(120), nullable=True),
        sa.Column("is_dev_placeholder", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "decision_cases",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "user_id",
            sa.String(36),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("domain", sa.String(40), nullable=False, server_default="other"),
        sa.Column("risk_level", sa.String(20), nullable=False, server_default="LOW"),
        sa.Column("status", sa.String(40), nullable=False, server_default="DRAFT", index=True),
        sa.Column("primary_stuck_type", sa.String(40), nullable=True),
        sa.Column("information_completeness", sa.Float(), nullable=True),
        sa.Column("decision_readiness", sa.Float(), nullable=True),
        sa.Column("selected_option_id", sa.String(36), nullable=True),
        sa.Column("committed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "decision_options",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "decision_case_id",
            sa.String(36),
            sa.ForeignKey("decision_cases.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("is_eligible", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("elimination_reason", sa.Text(), nullable=True),
        sa.Column("source", sa.String(40), nullable=False, server_default="user_input"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "decision_messages",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "decision_case_id",
            sa.String(36),
            sa.ForeignKey("decision_cases.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("role", sa.String(20), nullable=False, server_default="user"),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("decision_messages")
    op.drop_table("decision_options")
    op.drop_table("decision_cases")
    op.drop_table("users")
