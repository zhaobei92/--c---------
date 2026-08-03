import json

import httpx
import pytest
from pydantic import BaseModel

from model_gateway import (
    LLMClient,
    ModelCallError,
    ModelGateway,
    ModelGatewaySettings,
    ModelRole,
)


class Answer(BaseModel):
    value: str
    score: float


def make_settings() -> ModelGatewaySettings:
    return ModelGatewaySettings(
        MODEL_FAST="fast-model",
        LLM_API_BASE_URL="https://llm.example.com/v1",
        LLM_API_KEY="sk-test",
    )


def canned_transport(handler):
    return httpx.MockTransport(handler)


def test_structured_completion_parses_and_validates():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["payload"] = json.loads(request.content)
        captured["auth"] = request.headers.get("Authorization")
        body = {
            "choices": [
                {"message": {"content": json.dumps({"value": "ok", "score": 0.9})}}
            ]
        }
        return httpx.Response(200, json=body)

    client = LLMClient(make_settings(), transport=canned_transport(handler))
    result = client.structured_completion("fast-model", "system", "user", Answer)

    assert result == Answer(value="ok", score=0.9)
    assert captured["auth"] == "Bearer sk-test"
    assert captured["payload"]["model"] == "fast-model"
    assert captured["payload"]["temperature"] == 0
    assert captured["payload"]["response_format"]["type"] == "json_schema"


def test_invalid_schema_output_raises_model_call_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": json.dumps({"wrong": 1})}}]},
        )

    client = LLMClient(make_settings(), transport=canned_transport(handler))
    with pytest.raises(ModelCallError):
        client.structured_completion("fast-model", "system", "user", Answer)


def test_http_error_raises_model_call_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"error": "boom"})

    client = LLMClient(make_settings(), transport=canned_transport(handler))
    with pytest.raises(ModelCallError):
        client.structured_completion("fast-model", "system", "user", Answer)


def test_gateway_structured_requires_credentials():
    gw = ModelGateway(ModelGatewaySettings(MODEL_FAST="fast-model"))
    with pytest.raises(ModelCallError):
        gw.structured(ModelRole.FAST, "system", "user", Answer)


def test_retries_once_on_server_error_then_succeeds():
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(503, json={"error": "overloaded"})
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": json.dumps({"value": "ok", "score": 1.0})}}]},
        )

    client = LLMClient(make_settings(), transport=canned_transport(handler))
    result = client.structured_completion("fast-model", "s", "u", Answer)
    assert result.value == "ok"
    assert calls["n"] == 2


def test_retries_on_timeout_then_fails_cleanly():
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        raise httpx.ConnectTimeout("boom")

    client = LLMClient(make_settings(), transport=canned_transport(handler))
    with pytest.raises(ModelCallError):
        client.structured_completion("fast-model", "s", "u", Answer)
    assert calls["n"] == 2
