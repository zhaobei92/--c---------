"""阶段1的 Model Gateway 骨架。

只提供角色到模型名的解析和"未配置时安全失败"的语义；
真实的供应商调用（OpenAI 兼容 Responses / Anthropic Messages）在阶段2实现。
模型失败或未配置时抛出显式异常，调用方必须捕获并保证决策状态不被破坏。
"""

from model_gateway.settings import ModelGatewaySettings, ModelRole


class ModelNotConfiguredError(RuntimeError):
    def __init__(self, role: ModelRole):
        super().__init__(f"No model configured for role '{role}'. Set the MODEL_* env vars.")
        self.role = role


class ModelGateway:
    def __init__(self, settings: ModelGatewaySettings | None = None):
        self.settings = settings or ModelGatewaySettings()

    def resolve(self, role: ModelRole) -> str:
        model = self.settings.model_for(role)
        if not model:
            raise ModelNotConfiguredError(role)
        return model

    @property
    def is_configured(self) -> bool:
        return bool(self.settings.MODEL_DEFAULT and self.settings.LLM_API_KEY)
