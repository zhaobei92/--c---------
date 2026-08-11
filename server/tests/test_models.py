"""24 张表模型完整性:与计划清单一致,且可在 SQLite 上建表(生产 DDL 见 schema.sql)。"""

from pathlib import Path

from sqlalchemy import create_engine, inspect

from app.models import Base
from app.models.tables import ALL_TABLES

# 计划原 24 张 + outbox_events(P0-5 事务原子性引入)
EXPECTED = {
    "users", "user_identities", "devices", "device_bindings", "firmware_versions",
    "recordings", "media_assets", "upload_parts", "transcription_jobs",
    "transcript_segments", "speakers", "summaries", "summary_templates",
    "translations", "folders", "tags", "subscriptions", "entitlements",
    "usage_ledger", "orders", "consent_logs", "audit_logs",
    "deletion_requests", "notification_jobs", "outbox_events",
}


def test_all_core_tables_declared():
    assert set(ALL_TABLES) == EXPECTED
    assert len(EXPECTED) == 25
    assert set(Base.metadata.tables.keys()) == EXPECTED


def test_tables_create_on_sqlite():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    assert set(inspect(engine).get_table_names()) == EXPECTED


def test_schema_sql_covers_all_tables():
    sql = (Path(__file__).resolve().parent.parent / "migrations" / "schema.sql").read_text()
    for table in EXPECTED:
        assert f"CREATE TABLE {table} (" in sql, f"schema.sql missing {table}"
