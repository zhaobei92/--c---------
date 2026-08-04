"""P0-4 收尾:用户级去重必须落到数据库层,不能只在内存服务。

要求:
  * media_assets 有 user_id 外键;
  * 唯一约束为 (user_id, sha256),sha256 不再全局唯一;
  * schema.sql 与 SQLAlchemy 模型一致;
  * 不同用户可存同一 sha256,同一用户重复插入被约束拒绝。
"""

from pathlib import Path

import pytest
from sqlalchemy import create_engine, insert
from sqlalchemy.exc import IntegrityError

from app.models import Base
from app.models.tables import MediaAsset, User

SCHEMA = (Path(__file__).resolve().parent.parent / "migrations" / "schema.sql").read_text()


def _media_block() -> str:
    start = SCHEMA.index("CREATE TABLE media_assets")
    return SCHEMA[start: SCHEMA.index(";", start)]


def test_schema_sql_media_assets_user_scoped():
    block = _media_block()
    assert "user_id" in block, "media_assets 缺少 user_id"
    assert "UNIQUE (user_id, sha256)" in block
    assert "sha256          text NOT NULL UNIQUE" not in block
    assert "sha256 text NOT NULL UNIQUE" not in block


def test_model_media_assets_user_scoped():
    table = MediaAsset.__table__
    assert "user_id" in table.c, "模型缺少 user_id"
    assert not table.c.sha256.unique, "sha256 不得全局唯一"
    uniques = [
        {c.name for c in cons.columns}
        for cons in table.constraints
        if cons.__class__.__name__ == "UniqueConstraint"
    ]
    assert {"user_id", "sha256"} in uniques


def test_constraint_enforced_on_engine():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with engine.begin() as conn:
        conn.execute(insert(User).values(id="u1", email="a@t.co"))
        conn.execute(insert(User).values(id="u2", email="b@t.co"))
        conn.execute(insert(MediaAsset).values(
            id="m1", user_id="u1", sha256="s" * 64, storage_key="k1", size_bytes=1))
        # 不同用户,同一 sha256:允许
        conn.execute(insert(MediaAsset).values(
            id="m2", user_id="u2", sha256="s" * 64, storage_key="k2", size_bytes=1))
    with pytest.raises(IntegrityError):
        with engine.begin() as conn:
            conn.execute(insert(MediaAsset).values(
                id="m3", user_id="u1", sha256="s" * 64, storage_key="k3", size_bytes=1))
