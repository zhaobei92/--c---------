"""阶段4：评估录入 + 决策计算引擎的集成测试。"""

INPUT_TEXT = "买显示器，我在雷鸟U8和三星S27B800之间纠结，预算不超过5000元，主要办公。"


def _to_evidence_stage(client, text=INPUT_TEXT):
    """走完录入→诊断→比较→追问，停在可评估状态。"""
    decision_id = client.post(
        "/api/v1/decisions", json={"input": text, "input_type": "text"}
    ).json()["decision_id"]
    client.post(f"/api/v1/decisions/{decision_id}/analyze")
    client.post(f"/api/v1/decisions/{decision_id}/advance")  # 诊断
    client.post(f"/api/v1/decisions/{decision_id}/advance")  # 触发标准生成
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
    # 走完追问轮（一律回答）
    for _ in range(8):
        body = client.post(f"/api/v1/decisions/{decision_id}/advance").json()
        if body["kind"] == "question":
            client.post(
                f"/api/v1/decisions/{decision_id}/messages",
                json={"content": "都还好，没有特别的限制。"},
            )
        else:
            break
    return decision_id


def _fill_evaluations(client, decision_id, favor_first=True):
    detail = client.get(f"/api/v1/decisions/{decision_id}").json()
    items = []
    for i, option in enumerate(detail["options"]):
        for criterion in detail["criteria"]:
            high = 0.85 if (i == 0) == favor_first else 0.35
            items.append(
                {
                    "option_id": option["id"],
                    "criterion_id": criterion["id"],
                    "expected_value": high,
                    "uncertainty": 0.05,
                }
            )
    resp = client.put(
        f"/api/v1/decisions/{decision_id}/evaluations", json={"evaluations": items}
    )
    assert resp.status_code == 200
    return detail


def test_full_flow_reaches_evaluations_then_compute(client):
    decision_id = _to_evidence_stage(client)
    body = client.post(f"/api/v1/decisions/{decision_id}/advance").json()
    assert body["kind"] == "evaluations_needed"

    detail = _fill_evaluations(client, decision_id)
    body = client.post(f"/api/v1/decisions/{decision_id}/advance").json()
    assert body["kind"] == "ready_to_compute"

    resp = client.post(f"/api/v1/decisions/{decision_id}/compute")
    assert resp.status_code == 200
    run = resp.json()
    # 第一个选项被全面打高分 → 必胜
    assert run["winning_option_id"] == detail["options"][0]["id"]
    assert run["ranking_stability"] > 0.9
    result = run["result_snapshot"]
    assert result["algorithm_version"] == "engine-0.2.0"
    assert set(result["winner_probability"].keys()) == {
        o["id"] for o in detail["options"]
    }
    assert result["max_regret"]
    # 状态推进到敏感性分析
    assert (
        client.get(f"/api/v1/decisions/{decision_id}").json()["status"]
        == "SENSITIVITY_ANALYSIS"
    )


def test_compute_is_reproducible_with_fixed_seed(client):
    decision_id = _to_evidence_stage(client)
    _fill_evaluations(client, decision_id)
    r1 = client.post(f"/api/v1/decisions/{decision_id}/compute").json()
    r2 = client.post(f"/api/v1/decisions/{decision_id}/compute").json()
    assert r1["result_snapshot"] == r2["result_snapshot"]


def test_compute_requires_two_eligible_options(client):
    decision_id = _to_evidence_stage(client)
    detail = client.get(f"/api/v1/decisions/{decision_id}").json()
    # 淘汰一个选项后不足两个
    client.patch(
        f"/api/v1/decisions/{decision_id}/options/{detail['options'][0]['id']}",
        json={"is_eligible": False, "elimination_reason": "超出预算"},
    )
    resp = client.post(f"/api/v1/decisions/{decision_id}/compute")
    assert resp.status_code == 409


def test_user_ineligible_option_never_wins(client):
    decision_id = _to_evidence_stage(client)
    detail = _fill_evaluations(client, decision_id, favor_first=True)
    # 给案例加第三个选项并标记不合格（评分最高也不许赢）
    resp = client.post(
        f"/api/v1/decisions/{decision_id}/options",
        json={"name": "LG C4", "description": "越级选手"},
    )
    third_id = resp.json()["id"]
    items = [
        {
            "option_id": third_id,
            "criterion_id": c["id"],
            "expected_value": 0.99,
            "uncertainty": 0.02,
        }
        for c in detail["criteria"]
    ]
    client.put(f"/api/v1/decisions/{decision_id}/evaluations", json={"evaluations": items})
    client.patch(
        f"/api/v1/decisions/{decision_id}/options/{third_id}",
        json={"is_eligible": False, "elimination_reason": "超出预算"},
    )
    run = client.post(f"/api/v1/decisions/{decision_id}/compute").json()
    assert run["winning_option_id"] != third_id
    eliminated = run["result_snapshot"]["eliminated"]
    assert any(e["option_key"] == third_id for e in eliminated)


def test_evaluation_upsert_overwrites(client):
    decision_id = _to_evidence_stage(client)
    detail = client.get(f"/api/v1/decisions/{decision_id}").json()
    pair = {
        "option_id": detail["options"][0]["id"],
        "criterion_id": detail["criteria"][0]["id"],
    }
    client.put(
        f"/api/v1/decisions/{decision_id}/evaluations",
        json={"evaluations": [{**pair, "expected_value": 0.2}]},
    )
    client.put(
        f"/api/v1/decisions/{decision_id}/evaluations",
        json={"evaluations": [{**pair, "expected_value": 0.9}]},
    )
    evals = client.get(f"/api/v1/decisions/{decision_id}/evaluations").json()
    matching = [
        e
        for e in evals
        if e["option_id"] == pair["option_id"] and e["criterion_id"] == pair["criterion_id"]
    ]
    assert len(matching) == 1
    assert matching[0]["expected_value"] == 0.9


def test_unknown_pair_rejected(client):
    decision_id = _to_evidence_stage(client)
    resp = client.put(
        f"/api/v1/decisions/{decision_id}/evaluations",
        json={
            "evaluations": [
                {
                    "option_id": "00000000-0000-0000-0000-000000000000",
                    "criterion_id": "00000000-0000-0000-0000-000000000000",
                    "expected_value": 0.5,
                }
            ]
        },
    )
    assert resp.status_code == 422
