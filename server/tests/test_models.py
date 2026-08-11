"""24 张表模型完整性:与计划清单一致,且可在 SQLite 上建表(生产 DDL 见 schema.sql)。"""

from pathlib import Path

from sqlalchemy import create_engine, inspect

from app.models import Base
from app.models.tables import ALL_TABLES

# 计划原 24 张 + outbox_events(P0-5)+ upload_sessions(0002,S3 会话持久化)
BASELINE_TABLES = {
    "users", "user_identities", "devices", "device_bindings", "firmware_versions",
    "recordings", "media_assets", "upload_parts", "transcription_jobs",
    "transcript_segments", "speakers", "summaries", "summary_templates",
    "translations", "folders", "tags", "subscriptions", "entitlements",
    "usage_ledger", "orders", "consent_logs", "audit_logs",
    "deletion_requests", "notification_jobs", "outbox_events",
}
EXPECTED = BASELINE_TABLES | {"upload_sessions"}


def test_all_core_tables_declared():
    assert set(ALL_TABLES) == EXPECTED
    assert len(EXPECTED) == 26
    assert set(Base.metadata.tables.keys()) == EXPECTED


def test_tables_create_on_sqlite():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    assert set(inspect(engine).get_table_names()) == EXPECTED


def test_schema_sql_covers_baseline_tables():
    """schema.sql 已随 0001 冻结,只覆盖基线表;0002 起的表在 alembic versions。"""
    sql = (Path(__file__).resolve().parent.parent / "migrations" / "schema.sql").read_text()
    for table in BASELINE_TABLES:
        assert f"CREATE TABLE {table} (" in sql, f"schema.sql missing {table}"
    assert "CREATE TABLE upload_sessions" not in sql, "0002 的表禁止回写冻结的 schema.sql"
    migration = (Path(__file__).resolve().parent.parent / "alembic" / "versions"
                 / "0002_upload_sessions_outbox_delivery.py").read_text()
    assert "upload_sessions" in migration
