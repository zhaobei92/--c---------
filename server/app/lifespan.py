"""FastAPI lifespan:启动时校验配置并接线真实后端。"""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI

from .api.deps import state
from .bootstrap import configure_runtime
from .core.config import settings, validate_production_settings


@asynccontextmanager
async def lifespan(app: FastAPI):
    validate_production_settings(settings)  # prod 带开发态默认配置直接拒绝启动
    configure_runtime(state, settings)      # 非 memory 后端连接失败抛错终止进程
    yield
