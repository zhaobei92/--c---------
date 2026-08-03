"""阶段1的 Model Gateway 骨架。

只提供角色到模型名的解析和"未配置时安全失败"的语义；
真实的供应商调用（OpenAI 兼容 Responses / Anthropic Messages）在阶段2实现。
模型失败或未配置时抛出显式异常，调用方必须捕获并保证决策状态不被破坏。
"""

from typing import TypeVar

from pydantic import BaseModel

from model_gateway.client import LLMClient, ModelCallError
from model_gateway.settings import ModelGatewaySettings, ModelRole

T = TypeVar("T", bound=BaseModel)


class ModelNotConfiguredError(RuntimeError):
    def __init__(self, role: ModelRole):
        super().__init__(f"No model configured for role '{role}'. Set the MODEL_* env vars.")
        self.role = role


class ModelGateway:
    def __init__(
        self,
        settings: ModelGatewaySettings | None = None,
        client: LLMClient | None = None,
    ):
        self.settings = settings or ModelGatewaySettings()
        self._client = client

    def resolve(self, role: ModelRole) -> str:
        model = self.settings.model_for(role)
        if not model:
            raise ModelNotConfiguredError(role)
        return model

    @property
    def is_configured(self) -> bool:
        return bool(
            self.settings.MODEL_DEFAULT
            and self.settings.LLM_API_KEY
            and self.settings.LLM_API_BASE_URL
        )

    def role_configured(self, role: ModelRole) -> bool:
        return bool(
            self.settings.model_for(role)
            and self.settings.LLM_API_KEY
            and self.settings.LLM_API_BASE_URL
        )

    def _get_client(self) -> LLMClient:
        if self._client is None:
            self._client = LLMClient(self.settings)
        return self._client

    def structured(
        self, role: ModelRole, system_prompt: str, user_content: str, schema: type[T]
    ) -> T:
        """按角色路由模型并强制结构化输出。

        抛出 ModelNotConfiguredError / ModelCallError，调用方必须降级处理。
        """
        model = self.resolve(role)
        if not self.role_configured(role):
            raise ModelCallError("LLM API credentials not configured")
        return self._get_client().structured_completion(
            model=model, system_prompt=system_prompt, user_content=user_content, schema=schema
        )
