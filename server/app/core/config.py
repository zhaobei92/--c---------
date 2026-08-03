"""服务端配置(pydantic-settings;环境变量前缀 YS_)。"""

from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="YS_", env_file=".env", extra="ignore")

    env: str = "dev"  # dev / staging / prod
    database_url: str = "postgresql+psycopg://ys:ys@localhost:5432/ysnote"
    redis_url: str = "redis://localhost:6379/0"
    s3_endpoint: str = "http://localhost:9000"
    s3_bucket: str = "ysnote-audio"
    jwt_secret: str = "dev-secret-change-me"
    jwt_access_ttl_seconds: int = 3600
    jwt_refresh_ttl_seconds: int = 30 * 86400
    free_monthly_minutes: int = 120           # 注册即送,月度重置
    max_concurrent_jobs_free: int = 2
    max_concurrent_jobs_member: int = 5
    deletion_cooling_days: int = 7            # 注销冷静期
    apple_bundle_id: str = "com.ysnote.app"
    google_package_name: str = "com.ysnote.app"


settings = Settings()
