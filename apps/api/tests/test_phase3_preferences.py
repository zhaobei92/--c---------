"""阶段3：标准生成 + 成对比较 + Bradley-Terry 权重（LLM 未配置 → 领域默认标准）。"""

INPUT_TEXT = "买显示器，我在雷鸟U8和三星S27B800之间纠结，预算不超过5000元，主要办公。"


def _to_preference_stage(client, text=INPUT_TEXT):
    decision_id = client.post(
        "/api/v1/decisions", json={"input": text, "input_type": "text"}
    ).json()["decision_id"]
    client.post(f"/api/v1/decisions/{decision_id}/analyze")
    client.post(f"/api/v1/decisions/{decision_id}/advance")  # 诊断 → PREFERENCE_ELICITATION
    return decision_id


def test_advance_generates_criteria_and_requests_comparison(client):
    decision_id = _to_preference_stage(client)
    body = client.post(f"/api/v1/decisions/{decision_id}/advance").json()
    assert body["kind"] == "comparison_needed"

    detail = client.get(f"/api/v1/decisions/{decision_id}").json()
    # product 领域默认标准（LLM 未配置时）
    names = [c["name"] for c in detail["criteria"]]
    assert "价格" in names
    assert len(names) >= 3
    # 标准生成不携带 learned 权重
    assert all(c["learned_weight"] is None for c in detail["criteria"])


def test_comparison_flow_produces_explainable_weights(client):
    decision_id = _to_preference_stage(client)
    client.post(f"/api/v1/decisions/{decision_id}/advance")

    state = None
    for _ in range(10):
        nxt = client.get(f"/api/v1/decisions/{decision_id}/comparisons/next").json()
        if nxt["done"]:
            break
        # 总是选左边、强度0.8
        resp = client.post(
            f"/api/v1/decisions/{decision_id}/comparisons",
            json={
                "left_criterion_id": nxt["left"]["id"],
                "right_criterion_id": nxt["right"]["id"],
                "choice": "left",
                "strength": 0.8,
            },
        )
        assert resp.status_code == 201
        state = resp.json()

    assert state is not None
    assert state["comparisons_done"] >= state["comparisons_required"] >= 3
    weights = [c["learned_weight"] for c in state["criteria"]]
    assert all(w is not None for w in weights)
    assert abs(sum(weights) - 1.0) < 0.01
    assert state["consistency"] == 1.0

    # 比较完成后 advance 应放行到追问/评估阶段
    body = client.post(f"/api/v1/decisions/{decision_id}/advance").json()
    assert body["kind"] in ("question", "evaluations_needed", "ready_to_compute")
    detail = client.get(f"/api/v1/decisions/{decision_id}").json()
    assert detail["status"] == "EVIDENCE_GAP_ANALYSIS"


def test_conflicting_comparisons_lower_consistency(client):
    decision_id = _to_preference_stage(client)
    client.post(f"/api/v1/decisions/{decision_id}/advance")
    nxt = client.get(f"/api/v1/decisions/{decision_id}/comparisons/next").json()
    left, right = nxt["left"]["id"], nxt["right"]["id"]

    client.post(
        f"/api/v1/decisions/{decision_id}/comparisons",
        json={"left_criterion_id": left, "right_criterion_id": right, "choice": "left", "strength": 0.9},
    )
    state = client.post(
        f"/api/v1/decisions/{decision_id}/comparisons",
        json={"left_criterion_id": left, "right_criterion_id": right, "choice": "right", "strength": 0.9},
    ).json()

    assert state["consistency"] < 1.0
    # 冲突时对应标准的不确定度必须显著存在
    by_id = {c["id"]: c for c in state["criteria"]}
    assert by_id[left]["weight_uncertainty"] > 0.5


def test_invalid_pair_rejected(client):
    decision_id = _to_preference_stage(client)
    client.post(f"/api/v1/decisions/{decision_id}/advance")
    nxt = client.get(f"/api/v1/decisions/{decision_id}/comparisons/next").json()
    resp = client.post(
        f"/api/v1/decisions/{decision_id}/comparisons",
        json={
            "left_criterion_id": nxt["left"]["id"],
            "right_criterion_id": nxt["left"]["id"],
            "choice": "left",
            "strength": 0.5,
        },
    )
    assert resp.status_code == 422


def test_criteria_generation_idempotent(client):
    decision_id = _to_preference_stage(client)
    client.post(f"/api/v1/decisions/{decision_id}/advance")
    first = client.get(f"/api/v1/decisions/{decision_id}").json()["criteria"]
    client.post(f"/api/v1/decisions/{decision_id}/advance")
    second = client.get(f"/api/v1/decisions/{decision_id}").json()["criteria"]
    assert len(first) == len(second)
