"""OpenAI 兼容 Chat Completions 客户端，强制 Structured Outputs。

约束：
- 输出必须通过调用方提供的 Pydantic Schema 验证，验证失败视为调用失败；
- 任何网络 / 解析 / 验证错误都收敛为 ModelCallError，调用方必须捕获，
  保证模型失败不破坏决策状态。
"""

from typing import TypeVar

import httpx
from pydantic import BaseModel, ValidationError

from model_gateway.settings import ModelGatewaySettings

T = TypeVar("T", bound=BaseModel)


class ModelCallError(RuntimeError):
    pass


class LLMClient:
    def __init__(
        self,
        settings: ModelGatewaySettings,
        transport: httpx.BaseTransport | None = None,
    ):
        if not settings.LLM_API_BASE_URL or not settings.LLM_API_KEY:
            raise ModelCallError("LLM_API_BASE_URL / LLM_API_KEY not configured")
        self._settings = settings
        self._client = httpx.Client(
            base_url=settings.LLM_API_BASE_URL.rstrip("/"),
            headers={"Authorization": f"Bearer {settings.LLM_API_KEY}"},
            timeout=settings.LLM_TIMEOUT_SECONDS,
            transport=transport,
        )

    # 5xx / 网络错误重试一次（方案 8.4 要求超时与重试）
    MAX_ATTEMPTS = 2

    def structured_completion(
        self,
        model: str,
        system_prompt: str,
        user_content: str,
        schema: type[T],
    ) -> T:
        payload = {
            "model": model,
            "temperature": 0,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_content},
            ],
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": schema.__name__,
                    "strict": True,
                    "schema": schema.model_json_schema(),
                },
            },
        }
        last_exc: Exception | None = None
        for attempt in range(self.MAX_ATTEMPTS):
            try:
                resp = self._client.post("/chat/completions", json=payload)
                if resp.status_code >= 500 and attempt < self.MAX_ATTEMPTS - 1:
                    last_exc = ModelCallError(f"server error {resp.status_code}")
                    continue
                resp.raise_for_status()
                content = resp.json()["choices"][0]["message"]["content"]
                return schema.model_validate_json(content)
            except (httpx.TimeoutException, httpx.TransportError) as exc:
                last_exc = exc  # 网络类错误：可重试
            except (httpx.HTTPError, ValidationError, KeyError, IndexError, ValueError) as exc:
                raise ModelCallError(f"structured completion failed: {exc}") from exc
        raise ModelCallError(f"structured completion failed after retries: {last_exc}")

    def close(self) -> None:
        self._client.close()
