"""数据库引擎与会话工厂(同步 SQLAlchemy;路由当前为同步函数,不为先进而异步)。"""

from __future__ import annotations

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from ..core.config import settings


def make_engine(url: str | None = None):
    return create_engine(url or settings.database_url, pool_pre_ping=True)


def make_session_factory(engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, expire_on_commit=False)
