"""YS Note API 入口。

启动:uvicorn app.main:app --reload
OpenAPI:/docs(与 docs/04-api-spec.md 对应)
"""

from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from .api import admin, auth, billing, devices, jobs, notifications, recordings, uploads
from .core.config import settings, validate_production_settings
from .core.errors import ApiError

# P0-10:prod 环境带开发态默认配置直接拒绝启动
validate_production_settings(settings)

app = FastAPI(title="YS Note API", version="0.1.0")


@app.exception_handler(ApiError)
async def api_error_handler(_: Request, exc: ApiError) -> JSONResponse:
    return JSONResponse(status_code=exc.definition.http_status, content=exc.to_body())


@app.get("/health")
def health():
    return {"status": "ok", "env": settings.env}


for router in (auth.router, devices.router, recordings.router, uploads.router,
               jobs.router, billing.router, notifications.router, admin.router):
    app.include_router(router)
