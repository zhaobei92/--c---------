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
