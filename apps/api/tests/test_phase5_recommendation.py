"""阶段5：推荐解释 + Challenger 的集成测试。"""

import json

import httpx

from app.gateway import get_model_gateway
from model_gateway import LLMClient, ModelGateway, ModelGatewaySettings

INPUT_TEXT = "买显示器，我在雷鸟U8和三星S27B800之间纠结，预算不超过5000元，主要办公。"


def _prepare_computed(client, favor_first=True, uncertainty=0.05, spread=True):
    decision_id = client.post(
        "/api/v1/decisions", json={"input": INPUT_TEXT, "input_type": "text"}
    ).json()["decision_id"]
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
                f"/api/v1/decisions/{decision_id}/messages",
                json={"content": "都还好。"},
            )
        else:
            break

    detail = client.get(f"/api/v1/decisions/{decision_id}").json()
    items = []
    for i, option in enumerate(detail["options"]):
        for j, criterion in enumerate(detail["criteria"]):
            if spread:
                value = 0.85 if (i == 0) == favor_first else 0.35
                # 制造一个明确短板：第一名在最后一个标准上落后
                if i == 0 and j == len(detail["criteria"]) - 1:
                    value = 0.3
                if i == 1 and j == len(detail["criteria"]) - 1:
                    value = 0.8
            else:
                value = 0.51 if i == 0 else 0.5  # 势均力敌 → 效用差极小，必触发 Challenger
            items.append(
                {
                    "option_id": option["id"],
                    "criterion_id": criterion["id"],
                    "expected_value": value,
                    "uncertainty": uncertainty,
                }
            )
    client.put(f"/api/v1/decisions/{decision_id}/evaluations", json={"evaluations": items})
    client.post(f"/api/v1/decisions/{decision_id}/compute")
    return decision_id, detail


def test_recommendation_shows_benefits_and_costs(client):
    decision_id, detail = _prepare_computed(client)
    resp = client.post(f"/api/v1/decisions/{decision_id}/recommendation")
    assert resp.status_code == 200
    rec = resp.json()

    # 推荐必须来自算法赢家
    run = client.get(f"/api/v1/decisions/{decision_id}/runs/latest").json()
    assert rec["recommended_option_id"] == run["winning_option_id"]
    # 收益与代价同时展示
    assert len(rec["main_reasons"]) >= 1
    assert len(rec["accepted_tradeoffs"]) >= 1
    assert any("代价" in t or "不如" in t for t in rec["accepted_tradeoffs"])
    # 重开与非重开条件必须存在
    assert len(rec["reopen_conditions"]) >= 1
    assert len(rec["non_reopen_conditions"]) >= 1
    assert rec["next_action"]
    # 状态推进到 READY_TO_COMMIT
    assert (
        client.get(f"/api/v1/decisions/{decision_id}").json()["status"]
        == "READY_TO_COMMIT"
    )


def test_unstable_result_triggers_challenger_and_it_cannot_override(client):
    """势均力敌 → 稳定性低 → 调用 Challenger；Challenger 无法改变推荐。"""
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        calls.append(payload["model"])
        # Challenger 试图"推翻"推荐（输出无效字段无法做到，只能提问题）
        challenge = {
            "missing_assumptions": ["假设办公时长占比稳定"],
            "possible_biases": [],
            "constraint_violations": [],
            "fragile_variables": ["价格敏感度"],
            "counterargument": "两个选项差距太小，建议补充实测数据。",
            "requires_recompute": False,
        }
        # decision_coach 请求也会走这里：返回改了 option id 的恶意输出
        if payload["model"] == "coach-model":
            body = {
                "recommended_option_id": "WRONG-OPTION-ID",
                "summary": "被篡改的总结",
                "main_reasons": ["x"],
                "accepted_tradeoffs": ["y"],
                "critical_unknowns": [],
                "reopen_conditions": ["z"],
                "non_reopen_conditions": ["w"],
                "next_action": "n",
            }
        else:
            body = challenge
        return httpx.Response(
            200, json={"choices": [{"message": {"content": json.dumps(body)}}]}
        )

    settings = ModelGatewaySettings(
        MODEL_DEFAULT="coach-model",
        MODEL_CHALLENGER="challenger-model",
        LLM_API_BASE_URL="https://llm.example.com/v1",
        LLM_API_KEY="sk-test",
    )
    gw = ModelGateway(
        settings, client=LLMClient(settings, transport=httpx.MockTransport(handler))
    )
    client.app.dependency_overrides[get_model_gateway] = lambda: gw
    try:
        decision_id, _ = _prepare_computed(client, spread=False, uncertainty=0.3)
        run = client.get(f"/api/v1/decisions/{decision_id}/runs/latest").json()
        # 确认满足 Challenger 触发条件（稳定性低或效用差小）
        assert (
            run["ranking_stability"] < 0.55
            or run["result_snapshot"]["expected_utility_gap"] < 0.05
        )
        rec = client.post(f"/api/v1/decisions/{decision_id}/recommendation").json()
    finally:
        client.app.dependency_overrides.pop(get_model_gateway, None)

    # Challenger 被调用且输出被记录
    assert "challenger-model" in calls
    assert rec["challenger_output"] is not None
    assert rec["challenger_output"]["counterargument"]
    # 模型篡改 recommended_option_id 的输出被整体丢弃
    assert rec["recommended_option_id"] == run["winning_option_id"]
    assert rec["summary"] != "被篡改的总结"
    assert rec["source"] == "deterministic"


def test_stable_result_skips_challenger(client):
    decision_id, _ = _prepare_computed(client)  # 一边倒 → 稳定
    rec = client.post(f"/api/v1/decisions/{decision_id}/recommendation").json()
    assert rec["challenger_output"] is None


def test_recommendation_requires_compute_first(client):
    decision_id = client.post(
        "/api/v1/decisions", json={"input": INPUT_TEXT, "input_type": "text"}
    ).json()["decision_id"]
    client.post(f"/api/v1/decisions/{decision_id}/analyze")
    resp = client.post(f"/api/v1/decisions/{decision_id}/recommendation")
    assert resp.status_code == 409


def test_advance_after_recommendation_points_to_result(client):
    decision_id, _ = _prepare_computed(client)
    client.post(f"/api/v1/decisions/{decision_id}/recommendation")
    body = client.post(f"/api/v1/decisions/{decision_id}/advance").json()
    assert body["kind"] == "recommendation_ready"
