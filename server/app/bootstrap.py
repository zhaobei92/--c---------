"""应用启动接线:按配置组装真实后端(Phase 2.1 第一优先级)。

uvicorn 启动时经 lifespan 调用 configure_runtime —— 集成/生产环境不再需要
测试代码手工 state.configure_db()。规则:
  * 显式配置的非 memory 后端连接失败 → 抛 RuntimeError 拒绝启动(任何环境);
  * staging/prod 禁止 memory(validate_production_settings 拦截);
  * 返回的 handles 供 /health/ready 逐项探活。
"""

from __future__ import annotations

from sqlalchemy import text

from .core.config import Settings


def configure_runtime(state, settings: Settings) -> dict:
    handles: dict = {"settings": settings}

    if settings.storage_backend == "postgres":
        if settings.object_backend != "s3":
            raise RuntimeError(
                "storage_backend=postgres requires object_backend=s3 "
                "(黄金主链上传会话依赖真实对象存储)")
        from .db.engine import make_engine
        from .services.s3_store import S3ObjectStore

        engine = make_engine(settings.database_url)
        try:
            with engine.connect() as conn:
                conn.execute(text("SELECT 1"))
        except Exception as e:
            raise RuntimeError(f"PostgreSQL unreachable: {e}") from e

        store = S3ObjectStore(
            endpoint=settings.s3_endpoint,
            access_key=settings.s3_access_key,
            secret_key=settings.s3_secret_key,
            bucket=settings.s3_bucket,
        )
        try:
            store.ensure_bucket()
        except Exception as e:
            raise RuntimeError(f"S3 unreachable: {e}") from e

        state.configure_db(engine, store)
        handles["engine"] = engine
        handles["s3"] = store
    elif settings.storage_backend != "memory":
        raise RuntimeError(f"unknown storage_backend {settings.storage_backend}")

    if settings.code_store_backend == "redis" or settings.queue_backend == "redis":
        import redis as redis_lib

        client = redis_lib.Redis.from_url(settings.redis_url, decode_responses=True)
        try:
            client.ping()
        except Exception as e:
            raise RuntimeError(f"Redis unreachable: {e}") from e
        handles["redis"] = client
        if settings.code_store_backend == "redis":
            from .services.code_store import RedisCodeStore

            state.code_store = RedisCodeStore(client)
        if settings.queue_backend == "redis":
            from .services.redis_queue import RedisStreamsQueue

            state.streams_queue = RedisStreamsQueue(client)
            handles["queue"] = state.streams_queue

    state.runtime_handles = handles
    return handles


def readiness_report(state) -> tuple[bool, dict]:
    """/health/ready:逐后端探活。memory 后端与未配置项标注为跳过。"""
    handles = getattr(state, "runtime_handles", None) or {}
    settings: Settings | None = handles.get("settings")
    report: dict[str, str] = {}
    ok = True

    def check(name: str, fn) -> None:
        nonlocal ok
        try:
            fn()
            report[name] = "ok"
        except Exception as e:
            report[name] = f"fail: {e}"[:200]
            ok = False

    if "engine" in handles:
        check("postgres", lambda: handles["engine"].connect().execute(text("SELECT 1")))
        check("s3", lambda: handles["s3"].client.head_bucket(
            Bucket=handles["s3"].bucket))
    else:
        report["postgres"] = report["s3"] = "memory"
    if "redis" in handles:
        check("redis", handles["redis"].ping)
    else:
        report["redis"] = "memory"
    if settings is not None and settings.smtp_host:
        from .services.email_provider import SmtpEmailProvider

        check("smtp", SmtpEmailProvider(settings).healthcheck)
    else:
        report["smtp"] = "unconfigured"
    return ok, report
