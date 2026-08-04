"""Alembic 环境:连接串取自应用配置(YS_DATABASE_URL)。

迁移规约:schema.sql 是 0001 基线的唯一来源;基线之后的任何结构变化
必须新增 revision,禁止回改基线或直接改库。
"""

from alembic import context
from sqlalchemy import create_engine

from app.core.config import settings
from app.models import Base

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    context.configure(url=settings.database_url, target_metadata=target_metadata,
                      literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    engine = create_engine(settings.database_url)
    with engine.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
