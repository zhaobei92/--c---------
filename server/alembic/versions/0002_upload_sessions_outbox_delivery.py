"""0002:upload_sessions 表 + outbox_events 投递控制列。

- upload_sessions:真实 S3 Multipart 会话持久化(API 重启后可恢复上传);
- outbox_events 增加 attempts / last_error / next_attempt_at,
  支撑 FOR UPDATE SKIP LOCKED 领取 + 失败退避重投。
schema.sql 已随 0001 冻结,本文件是这些结构的唯一定义处(模型同步于
app/models/tables.py,由 tests/test_models.py 与集成迁移测试双向守护)。
"""

import sqlalchemy as sa
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "upload_sessions",
        sa.Column("id", sa.dialects.postgresql.UUID(as_uuid=False),
                  primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("user_id", sa.dialects.postgresql.UUID(as_uuid=False),
                  sa.ForeignKey("users.id"), nullable=False),
        sa.Column("recording_id", sa.dialects.postgresql.UUID(as_uuid=False),
                  sa.ForeignKey("recordings.id"), nullable=False),
        sa.Column("storage_key", sa.Text, nullable=False),
        sa.Column("s3_upload_id", sa.Text, nullable=False),   # S3/MinIO Multipart Upload ID
        sa.Column("size_bytes", sa.BigInteger, nullable=False),
        sa.Column("part_size", sa.Integer, nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("status", sa.String(12), nullable=False, server_default="active"),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True),
                  nullable=False, server_default=sa.text("now()")),
        sa.Column("expires_at", sa.TIMESTAMP(timezone=True), nullable=False),
    )
    op.create_index("idx_upload_sessions_user", "upload_sessions",
                    ["user_id", "status"])
    op.create_index("idx_upload_sessions_expiry", "upload_sessions",
                    ["status", "expires_at"])

    op.add_column("outbox_events",
                  sa.Column("attempts", sa.Integer, nullable=False, server_default="0"))
    op.add_column("outbox_events", sa.Column("last_error", sa.Text, nullable=True))
    op.add_column("outbox_events",
                  sa.Column("next_attempt_at", sa.TIMESTAMP(timezone=True),
                            nullable=False, server_default=sa.text("now()")))


def downgrade() -> None:
    op.drop_column("outbox_events", "next_attempt_at")
    op.drop_column("outbox_events", "last_error")
    op.drop_column("outbox_events", "attempts")
    op.drop_table("upload_sessions")
