from enum import StrEnum

from pydantic_settings import BaseSettings, SettingsConfigDict


class ModelRole(StrEnum):
    FAST = "fast"
    DEFAULT = "default"
    DEEP = "deep"
    CHALLENGER = "challenger"
    EMBEDDING = "embedding"


class ModelGatewaySettings(BaseSettings):
    """模型名一律来自环境变量，业务代码禁止写死模型名。"""

    model_config = SettingsConfigDict(env_prefix="", extra="ignore")

    MODEL_FAST: str | None = None
    MODEL_DEFAULT: str | None = None
    MODEL_DEEP: str | None = None
    MODEL_CHALLENGER: str | None = None
    MODEL_EMBEDDING: str | None = None

    LLM_API_BASE_URL: str | None = None
    LLM_API_KEY: str | None = None
    LLM_TIMEOUT_SECONDS: float = 60.0

    def model_for(self, role: ModelRole) -> str | None:
        return {
            ModelRole.FAST: self.MODEL_FAST,
            ModelRole.DEFAULT: self.MODEL_DEFAULT,
            ModelRole.DEEP: self.MODEL_DEEP,
            ModelRole.CHALLENGER: self.MODEL_CHALLENGER,
            ModelRole.EMBEDDING: self.MODEL_EMBEDDING,
        }[role]
