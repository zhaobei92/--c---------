"""0001 基线:执行 migrations/schema.sql 全量建表。

基线包含(相对早期设计的两处修正,随 P0 修复进入基线):
  * media_assets 用户级唯一 UNIQUE(user_id, sha256)(P0-4);
  * usage_ledger.charge_generation 与 outbox_events 表(P0-5/P0-6)。
本项目尚未有生产数据库,故以当前 schema.sql 为基线;
自本 revision 起,任何结构变化必须新增 revision。
"""

from pathlib import Path

from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

_SCHEMA = Path(__file__).resolve().parents[2] / "migrations" / "schema.sql"


def upgrade() -> None:
    op.execute(_SCHEMA.read_text())


def downgrade() -> None:
    raise RuntimeError("baseline is not downgradable")
