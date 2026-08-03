"""审查修正项的回归测试：快速模式、删除记录、LLM 风险只升不降。"""

import json

import httpx

from app.gateway import get_model_gateway
from model_gateway import LLMClient, ModelGateway, ModelGatewaySettings

INPUT_TEXT = "买显示器，我在雷鸟U8和三星S27B800之间纠结，预算不超过5000元，主要办公。"


def _create_analyzed(client, text=INPUT_TEXT):
    decision_id = client.post(
        "/api/v1/decisions", json={"input": text, "input_type": "text"}
    ).json()["decision_id"]
    client.post(f"/api/v1/decisions/{decision_id}/analyze")
    return decision_id


def test_fast_track_skips_to_conclusion(client):
    """用户说"直接给结论"：跳过比较与追问，直接可计算并出推荐。"""
    decision_id = _create_analyzed(client)
    client.post(f"/api/v1/decisions/{decision_id}/advance")  # 诊断

    resp = client.post(f"/api/v1/decisions/{decision_id}/fast-track")
    assert resp.status_code == 200
    body = resp.json()
    assert body["kind"] == "ready_to_compute"
    assert "不确定性更高" in body["message"]

    # 无需评估也能直接计算（缺失值按中性高不确定处理）
    assert client.post(f"/api/v1/decisions/{decision_id}/compute").status_code == 200
    rec = client.post(f"/api/v1/decisions/{decision_id}/recommendation")
    assert rec.status_code == 200
    # 未比较 → 权重均匀且不确定度高
    detail = client.get(f"/api/v1/decisions/{decision_id}").json()
    uncertainties = [c["weight_uncertainty"] for c in detail["criteria"]]
    assert all(u is not None and u > 0.5 for u in uncertainties)


def test_fast_track_refused_for_guided_only(client):
    decision_id = _create_analyzed(client, "我在纠结要不要辞职，在留下和辞职之间摇摆。")
    resp = client.post(f"/api/v1/decisions/{decision_id}/fast-track")
    assert resp.status_code == 409


def test_delete_decision_removes_all_records(client):
    decision_id = _create_analyzed(client)
    assert client.delete(f"/api/v1/decisions/{decision_id}").status_code == 204
    assert client.get(f"/api/v1/decisions/{decision_id}").status_code == 404
    ids = [c["id"] for c in client.get("/api/v1/decisions").json()]
    assert decision_id not in ids


def _gateway_returning_risk(level: str):
    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        schema_name = payload["response_format"]["json_schema"]["name"]
        if schema_name == "RiskAssessment":
            body = {"risk_level": level, "rationale": "test"}
        elif schema_name == "IntakeExtraction":
            body = {
                "title": "T",
                "domain": "product",
                "options": [{"name": "A", "description": None}, {"name": "B", "description": None}],
                "facts": [],
                "constraints": [],
                "concerns": [],
                "unknowns": [],
                "clarification_required": False,
            }
        else:
            return httpx.Response(500, json={"error": "unused"})
        return httpx.Response(
            200, json={"choices": [{"message": {"content": json.dumps(body)}}]}
        )

    settings = ModelGatewaySettings(
        MODEL_FAST="fast-model",
        LLM_API_BASE_URL="https://llm.example.com/v1",
        LLM_API_KEY="sk-test",
    )
    return ModelGateway(
        settings, client=LLMClient(settings, transport=httpx.MockTransport(handler))
    )


def test_llm_can_raise_risk_level(client):
    """确定性 LOW + LLM 判 HIGH → 取 HIGH（宁可误报）。"""
    client.app.dependency_overrides[get_model_gateway] = lambda: _gateway_returning_risk("HIGH")
    try:
        decision_id = _create_analyzed(client, "我在A和B之间纠结。")
        detail = client.get(f"/api/v1/decisions/{decision_id}").json()
    finally:
        client.app.dependency_overrides.pop(get_model_gateway, None)
    assert detail["risk_level"] == "HIGH"
    assert detail["status"] == "GUIDED_ONLY"


def test_llm_cannot_lower_risk_level(client):
    """确定性 HIGH + LLM 判 LOW → 保持 HIGH（永不调低）。"""
    client.app.dependency_overrides[get_model_gateway] = lambda: _gateway_returning_risk("LOW")
    try:
        decision_id = _create_analyzed(
            client, "我在纠结要不要辞职，在留下和辞职之间摇摆。"
        )
        detail = client.get(f"/api/v1/decisions/{decision_id}").json()
    finally:
        client.app.dependency_overrides.pop(get_model_gateway, None)
    assert detail["risk_level"] == "HIGH"
    assert detail["status"] == "GUIDED_ONLY"
