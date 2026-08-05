"""迁移脚手架完整性:revision 可加载、基线指向 schema.sql、迁移链无断裂。"""

import pytest

pytest.importorskip("alembic")

from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory

ROOT = Path(__file__).resolve().parent.parent


def test_migration_chain_loads():
    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "alembic"))
    scripts = ScriptDirectory.from_config(cfg)
    heads = scripts.get_heads()
    assert len(heads) == 1, f"迁移链必须单头,当前 {heads}"
    revisions = list(scripts.walk_revisions())
    assert revisions[-1].revision == "0001"  # 基线存在


def test_baseline_references_schema_sql():
    baseline = (ROOT / "alembic" / "versions" / "0001_baseline.py").read_text()
    assert "schema.sql" in baseline
    assert (ROOT / "migrations" / "schema.sql").exists()


BASELINE_SHA256 = "83c3e5b7f83ff075c8016d5e67a469d129e67db5d4eac2b40bc969c2611d1c69"


def test_baseline_snapshot_is_frozen():
    """0001 基线快照不可变:内容哈希锁定,任何修改都会打红此测试。

    结构变化的唯一出路是新增 0002、0003… revision;
    禁止改 0001_baseline.py,禁止改 0001_schema.sql。
    """
    import hashlib

    snapshot = ROOT / "alembic" / "sql" / "0001_schema.sql"
    actual = hashlib.sha256(snapshot.read_bytes()).hexdigest()
    assert actual == BASELINE_SHA256, (
        "0001_schema.sql 被修改!基线迁移必须不可变,请回退修改并新增 revision")


def test_baseline_reads_snapshot_not_live_schema():
    baseline = (ROOT / "alembic" / "versions" / "0001_baseline.py").read_text()
    assert 'sql" / "0001_schema.sql' in baseline
    assert '"migrations" / "schema.sql"' not in baseline
