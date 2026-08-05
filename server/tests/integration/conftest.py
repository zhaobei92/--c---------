"""集成测试夹具:真实 PostgreSQL / Redis / S3(MinIO 或 moto)/ SMTP(Mailpit)。

端点全部来自环境变量(本地与 CI 同一套测试):
  YS_DATABASE_URL   默认 postgresql+psycopg://ys:ys@127.0.0.1:5432/ysnote
  YS_REDIS_URL      默认 redis://127.0.0.1:6379/0
  YS_S3_ENDPOINT    默认 http://127.0.0.1:9000(CI=MinIO;本地可用 moto server)
  YS_SMTP_HOST/PORT 默认 127.0.0.1:1025(Mailpit)
  MAILPIT_API       默认 http://127.0.0.1:8025
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

PG_URL = os.environ.get("YS_DATABASE_URL",
                        "postgresql+psycopg://ys:ys@127.0.0.1:5432/ysnote")
REDIS_URL = os.environ.get("YS_REDIS_URL", "redis://127.0.0.1:6379/0")
S3_ENDPOINT = os.environ.get("YS_S3_ENDPOINT", "http://127.0.0.1:9000")
S3_KEY = os.environ.get("YS_S3_ACCESS_KEY", "ysnote")
S3_SECRET = os.environ.get("YS_S3_SECRET_KEY", "ysnote-dev-secret")
SMTP_HOST = os.environ.get("YS_SMTP_HOST", "127.0.0.1")
SMTP_PORT = int(os.environ.get("YS_SMTP_PORT", "1025"))
MAILPIT_API = os.environ.get("MAILPIT_API", "http://127.0.0.1:8025")


def _alembic_config():
    from alembic.config import Config
    root = Path(__file__).resolve().parent.parent.parent
    cfg = Config(str(root / "alembic.ini"))
    cfg.set_main_option("script_location", str(root / "alembic"))
    return cfg


@pytest.fixture(scope="session")
def migrated_engine():
    """空库 → alembic upgrade head → 返回 engine(整个会话复用)。"""
    from alembic import command
    from sqlalchemy import create_engine, text

    engine = create_engine(PG_URL)
    with engine.begin() as conn:
        conn.execute(text("DROP SCHEMA public CASCADE"))
        conn.execute(text("CREATE SCHEMA public"))
    os.environ["YS_DATABASE_URL"] = PG_URL
    command.upgrade(_alembic_config(), "head")
    yield engine
    engine.dispose()


@pytest.fixture
def db(migrated_engine):
    """每个用例前清空业务数据(保留结构与 alembic_version)。"""
    from sqlalchemy import text

    with migrated_engine.begin() as conn:
        tables = conn.execute(text(
            "SELECT tablename FROM pg_tables "
            "WHERE schemaname='public' AND tablename <> 'alembic_version'"
        )).scalars().all()
        if tables:
            conn.execute(text(
                "TRUNCATE " + ", ".join(f'"{t}"' for t in tables) + " CASCADE"))
    return migrated_engine


@pytest.fixture(scope="session")
def redis_client():
    import redis as redis_lib

    client = redis_lib.Redis.from_url(REDIS_URL, decode_responses=True)
    client.ping()
    yield client
    client.close()


@pytest.fixture
def redis_db(redis_client):
    redis_client.flushdb()
    return redis_client


@pytest.fixture(scope="session")
def s3_client():
    import boto3

    client = boto3.client(
        "s3", endpoint_url=S3_ENDPOINT,
        aws_access_key_id=S3_KEY, aws_secret_access_key=S3_SECRET,
        region_name="us-east-1",
    )
    try:
        client.create_bucket(Bucket="ysnote-audio")
    except client.exceptions.BucketAlreadyOwnedByYou:
        pass
    except Exception:
        try:
            client.head_bucket(Bucket="ysnote-audio")
        except Exception:
            raise
    return client


@pytest.fixture
def mailpit():
    import requests

    class Mailpit:
        api = MAILPIT_API
        smtp = (SMTP_HOST, SMTP_PORT)

        def clear(self):
            requests.delete(f"{self.api}/api/v1/messages", timeout=5)

        def messages(self):
            r = requests.get(f"{self.api}/api/v1/messages", timeout=5)
            return r.json().get("messages", [])

        def body(self, message_id: str) -> str:
            r = requests.get(f"{self.api}/api/v1/message/{message_id}", timeout=5)
            return r.json().get("Text", "")

    m = Mailpit()
    m.clear()
    return m
