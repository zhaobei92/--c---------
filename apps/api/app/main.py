from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import admin, decisions, health, profile
from app.config import get_settings


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(title="定了 Dingle API", version="0.1.0")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(health.router)
    app.include_router(decisions.router)
    app.include_router(profile.router)
    app.include_router(admin.router)
    return app


app = create_app()
