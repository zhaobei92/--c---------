"""YS Note API 入口。

启动:uvicorn app.main:app --reload
OpenAPI:/docs(与 docs/04-api-spec.md 对应)
"""

from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from .api import (admin, auth, billing, devices, jobs, notifications,
                  recordings, transcripts, uploads)
from .api.deps import state
from .bootstrap import readiness_report
from .core.config import settings, validate_production_settings
from .core.errors import ApiError
from .lifespan import lifespan

# P0-10:导入期先行校验(lifespan 启动时再次执行,双保险)
validate_production_settings(settings)

app = FastAPI(title="YS Note API", version="0.1.0", lifespan=lifespan)


@app.exception_handler(ApiError)
async def api_error_handler(_: Request, exc: ApiError) -> JSONResponse:
    return JSONResponse(status_code=exc.definition.http_status, content=exc.to_body())


@app.get("/health")
def health():
    return {"status": "ok", "env": settings.env}


@app.get("/health/live")
def health_live():
    """进程存活探针:不检查依赖。"""
    return {"status": "alive"}


@app.get("/health/ready")
def health_ready():
    """就绪探针:逐项检查 PostgreSQL / Redis / S3 / SMTP(memory 后端标注跳过)。"""
    ok, report = readiness_report(state)
    status_code = 200 if ok else 503
    return JSONResponse(status_code=status_code,
                        content={"ready": ok, "checks": report})


for router in (auth.router, devices.router, recordings.router, uploads.router,
               jobs.router, billing.router, notifications.router,
               transcripts.router, admin.router):
    app.include_router(router)
