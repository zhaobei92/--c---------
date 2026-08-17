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
    admin_token: str = "dev-admin"                 # prod 必须覆盖(启动拦截)
    # 邮件服务(prod 必须配置真实 SMTP,布尔标记不算配置)
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_from: str = ""
    smtp_user: str = ""
    smtp_password: str = ""
    smtp_starttls: bool = True   # Mailpit 等明文测试服务设 false
    # 运行时后端选择(dev/test 允许 memory;staging/prod 禁止)
    storage_backend: str = "memory"      # memory | postgres
    queue_backend: str = "memory"        # memory | redis
    object_backend: str = "memory"       # memory | s3
    code_store_backend: str = "memory"   # memory | redis
    ai_provider: str = "mock"            # mock(Demo/审核)| noop | 真实供应商后续登记
    s3_access_key: str = "ysnote"
    s3_secret_key: str = "ysnote-dev-secret"


_DEFAULT_JWT_SECRET = "dev-secret-change-me"
_DEFAULT_ADMIN_TOKEN = "dev-admin"


def validate_production_settings(s: "Settings") -> None:
    """P0-10 启动拦截:env=prod 时拒绝任何开发态默认配置。

    在应用启动时调用(app.main);校验失败直接抛 RuntimeError 终止进程,
    绝不允许带默认密钥/默认管理 token/无邮件服务的实例暴露到公网。
    """
    if s.env != "prod":
        return
    problems: list[str] = []
    if s.jwt_secret == _DEFAULT_JWT_SECRET or len(s.jwt_secret) < 32:
        problems.append("jwt_secret is default or too short (need >=32 chars)")
    if s.admin_token == _DEFAULT_ADMIN_TOKEN or len(s.admin_token) < 16:
        problems.append("admin_token is default or too short (need >=16 chars)")
    if not s.smtp_host:
        problems.append("smtp_host not configured (real email provider required)")
    if not s.smtp_from:
        problems.append("smtp_from not configured (real email provider required)")
    for name, value in (("storage_backend", s.storage_backend),
                        ("queue_backend", s.queue_backend),
                        ("object_backend", s.object_backend),
                        ("code_store_backend", s.code_store_backend)):
        if value == "memory":
            problems.append(f"{name}=memory is forbidden in prod")
    if problems:
        raise RuntimeError("refusing to start in prod: " + "; ".join(problems))


settings = Settings()
