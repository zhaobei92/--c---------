"""真实 PostgreSQL 迁移测试(审查要求):

空库 → alembic upgrade head → 26 张表与关键索引存在 →
再次 upgrade head 无操作 → alembic current == head。
"""

import pytest
from sqlalchemy import inspect, text

pytestmark = pytest.mark.integration

EXPECTED_TABLES = {
    "users", "user_identities", "devices", "device_bindings", "firmware_versions",
    "recordings", "media_assets", "upload_parts", "transcription_jobs",
    "transcript_segments", "speakers", "summaries", "summary_templates",
    "translations", "folders", "tags", "subscriptions", "entitlements",
    "usage_ledger", "orders", "consent_logs", "audit_logs",
    "deletion_requests", "notification_jobs", "outbox_events", "upload_sessions",
}


def test_upgrade_head_creates_all_tables(migrated_engine):
    tables = set(inspect(migrated_engine).get_table_names()) - {"alembic_version"}
    assert tables == EXPECTED_TABLES
    assert len(EXPECTED_TABLES) == 26


def test_key_constraints_present(migrated_engine):
    with migrated_engine.connect() as conn:
        def indexdefs(table):
            return "\n".join(conn.execute(text(
                "SELECT indexdef FROM pg_indexes WHERE tablename=:t"), {"t": table}
            ).scalars())

        # 验收红线对应的约束
        assert "uq_device_bindings_active" in indexdefs("device_bindings")   # 绑定串号 0
        assert "uq_recordings_user_sha" in indexdefs("recordings")           # 重复文件 0
        assert "uq_jobs_active" in indexdefs("transcription_jobs")           # 重复任务 0
        assert "user_id" in indexdefs("media_assets")                        # 用户级去重
        idem = conn.execute(text(
            "SELECT COUNT(*) FROM pg_indexes WHERE tablename='usage_ledger' "
            "AND indexdef LIKE '%idempotency_key%'")).scalar()
        assert idem >= 1                                                     # 重复扣费 0
        cols = {r[0] for r in conn.execute(text(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name='outbox_events'"))}
        assert {"attempts", "last_error", "next_attempt_at", "published_at"} <= cols
        ledger_cols = {r[0] for r in conn.execute(text(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name='usage_ledger'"))}
        assert "charge_generation" in ledger_cols


def test_upgrade_head_idempotent(migrated_engine):
    import os
    from alembic import command
    from alembic.config import Config
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent.parent
    cfg = Config(str(root / "alembic.ini"))
    cfg.set_main_option("script_location", str(root / "alembic"))
    command.upgrade(cfg, "head")  # 第二次执行必须无操作、不报错


def test_alembic_current_is_head(migrated_engine):
    from alembic.script import ScriptDirectory
    from alembic.config import Config
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent.parent
    cfg = Config(str(root / "alembic.ini"))
    cfg.set_main_option("script_location", str(root / "alembic"))
    head = ScriptDirectory.from_config(cfg).get_current_head()
    with migrated_engine.connect() as conn:
        current = conn.execute(text("SELECT version_num FROM alembic_version")).scalar()
    assert current == head == "0002"
