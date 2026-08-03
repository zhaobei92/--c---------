"""故障注入（方案阶段8）：模型供应商故障不得破坏决策状态。"""

import json

import httpx

from app.gateway import get_model_gateway
from model_gateway import LLMClient, ModelGateway, ModelGatewaySettings

INPUT_TEXT = "买显示器，我在雷鸟U8和三星S27B800之间纠结，预算不超过5000元，主要办公。"


def _make_gateway(handler):
    settings = ModelGatewaySettings(
        MODEL_FAST="fast-model",
        MODEL_DEFAULT="default-model",
        MODEL_CHALLENGER="challenger-model",
        LLM_API_BASE_URL="https://llm.example.com/v1",
        LLM_API_KEY="sk-test",
    )
    return ModelGateway(
        settings, client=LLMClient(settings, transport=httpx.MockTransport(handler))
    )


def _run_full_flow(client, decision_id):
    client.post(f"/api/v1/decisions/{decision_id}/analyze")
    client.post(f"/api/v1/decisions/{decision_id}/advance")
    client.post(f"/api/v1/decisions/{decision_id}/advance")
    for _ in range(10):
        nxt = client.get(f"/api/v1/decisions/{decision_id}/comparisons/next").json()
        if nxt["done"]:
            break
        client.post(
            f"/api/v1/decisions/{decision_id}/comparisons",
            json={
                "left_criterion_id": nxt["left"]["id"],
                "right_criterion_id": nxt["right"]["id"],
                "choice": "left",
                "strength": 0.7,
            },
        )
    for _ in range(8):
        body = client.post(f"/api/v1/decisions/{decision_id}/advance").json()
        if body["kind"] == "question":
            client.post(
                f"/api/v1/decisions/{decision_id}/messages", json={"content": "都还好。"}
            )
        else:
            break
    detail = client.get(f"/api/v1/decisions/{decision_id}").json()
    items = [
        {
            "option_id": o["id"],
            "criterion_id": c["id"],
            "expected_value": 0.8 if i == 0 else 0.4,
            "uncertainty": 0.05,
        }
        for i, o in enumerate(detail["options"])
        for c in detail["criteria"]
    ]
    client.put(f"/api/v1/decisions/{decision_id}/evaluations", json={"evaluations": items})
    client.post(f"/api/v1/decisions/{decision_id}/compute")
    return client.post(f"/api/v1/decisions/{decision_id}/recommendation")


def test_total_provider_outage_degrades_but_completes(client):
    """供应商全程 500：全流程仍能走到推荐，状态不破坏。"""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"error": "provider down"})

    client.app.dependency_overrides[get_model_gateway] = lambda: _make_gateway(handler)
    try:
        decision_id = client.post(
            "/api/v1/decisions", json={"input": INPUT_TEXT, "input_type": "text"}
        ).json()["decision_id"]
        resp = _run_full_flow(client, decision_id)
        assert resp.status_code == 200
        rec = resp.json()
        assert rec["source"] == "deterministic"
        detail = client.get(f"/api/v1/decisions/{decision_id}").json()
        assert detail["status"] == "READY_TO_COMMIT"
    finally:
        client.app.dependency_overrides.pop(get_model_gateway, None)


def test_provider_timeout_degrades_but_completes(client):
    """供应商超时（连接异常）：同样降级完成。"""

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("simulated timeout")

    client.app.dependency_overrides[get_model_gateway] = lambda: _make_gateway(handler)
    try:
        decision_id = client.post(
            "/api/v1/decisions", json={"input": INPUT_TEXT, "input_type": "text"}
        ).json()["decision_id"]
        resp = _run_full_flow(client, decision_id)
        assert resp.status_code == 200
    finally:
        client.app.dependency_overrides.pop(get_model_gateway, None)


def test_intermittent_failures_recover_midway(client):
    """间歇性故障：部分调用成功、部分失败，流程不中断且成功调用生效。"""
    call_count = {"n": 0}

    intake_payload = {
        "title": "显示器选择",
        "domain": "product",
        "options": [
            {"name": "雷鸟U8", "description": None},
            {"name": "三星S27B800", "description": None},
        ],
        "facts": ["主要办公"],
        "constraints": [{"description": "预算不超过5000元", "is_hard": True}],
        "concerns": [],
        "unknowns": ["影音占比"],
        "clarification_required": False,
    }

    def handler(request: httpx.Request) -> httpx.Response:
        call_count["n"] += 1
        if call_count["n"] == 1:
            return httpx.Response(
                200,
                json={
                    "choices": [
                        {"message": {"content": json.dumps(intake_payload)}}
                    ]
                },
            )
        return httpx.Response(500, json={"error": "flaky"})

    client.app.dependency_overrides[get_model_gateway] = lambda: _make_gateway(handler)
    try:
        decision_id = client.post(
            "/api/v1/decisions", json={"input": INPUT_TEXT, "input_type": "text"}
        ).json()["decision_id"]
        resp = _run_full_flow(client, decision_id)
        assert resp.status_code == 200
        detail = client.get(f"/api/v1/decisions/{decision_id}").json()
        # 第一次成功的 LLM 提取生效
        assert detail["title"] == "显示器选择"
        assert detail["status"] == "READY_TO_COMMIT"
    finally:
        client.app.dependency_overrides.pop(get_model_gateway, None)
