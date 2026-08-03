"""阶段7：回访与长期偏好后验的集成测试。"""

from datetime import datetime, timedelta, timezone

INPUT_TEXT = "买显示器，我在雷鸟U8和三星S27B800之间纠结，预算不超过5000元，主要办公。"


def _committed_case(client, favor_price_strength=0.9):
    """走完全流程并锁定决定。favor_price_strength 控制第一个标准（价格）的偏好强度。"""
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
        # 价格总是赢 → 价格权重高
        first_is_left = nxt["left"]["name"] == "价格"
        client.post(
            f"/api/v1/decisions/{decision_id}/comparisons",
            json={
                "left_criterion_id": nxt["left"]["id"],
                "right_criterion_id": nxt["right"]["id"],
                "choice": "left" if first_is_left else ("right" if nxt["right"]["name"] == "价格" else "left"),
                "strength": favor_price_strength,
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
            "expected_value": 0.85 if i == 0 else 0.4,
            "uncertainty": 0.05,
        }
        for i, o in enumerate(detail["options"])
        for c in detail["criteria"]
    ]
    client.put(f"/api/v1/decisions/{decision_id}/evaluations", json={"evaluations": items})
    client.post(f"/api/v1/decisions/{decision_id}/compute")
    rec = client.post(f"/api/v1/decisions/{decision_id}/recommendation").json()
    client.post(
        f"/api/v1/decisions/{decision_id}/commit",
        json={
            "selected_option_id": rec["recommended_option_id"],
            "accepted_tradeoffs": True,
        },
    )
    return decision_id


def test_followup_recording_and_state(client):
    decision_id = _committed_case(client)
    resp = client.post(
        f"/api/v1/decisions/{decision_id}/followups",
        json={"checkpoint": "h24", "executed": True, "notes": "已下单"},
    )
    assert resp.status_code == 201
    assert client.get(f"/api/v1/decisions/{decision_id}").json()["status"] == "FOLLOW_UP"

    # 同一节点不能重复提交
    resp = client.post(
        f"/api/v1/decisions/{decision_id}/followups",
        json={"checkpoint": "h24", "executed": True},
    )
    assert resp.status_code == 409

    resp = client.post(
        f"/api/v1/decisions/{decision_id}/followups",
        json={"checkpoint": "d7", "satisfaction": 0.8, "regret_level": 0.1},
    )
    assert resp.status_code == 201
    followups = client.get(f"/api/v1/decisions/{decision_id}/followups").json()
    assert {f["checkpoint"] for f in followups} == {"h24", "d7"}


def test_due_checkpoints_computed_from_committed_at(client):
    decision_id = _committed_case(client)
    now = client.get(f"/api/v1/decisions/{decision_id}/followups/due").json()
    assert now["due"] == []  # 刚提交，都未到期

    future = (datetime.now(timezone.utc) + timedelta(days=8)).isoformat()
    later = client.get(
        f"/api/v1/decisions/{decision_id}/followups/due", params={"as_of": future}
    ).json()
    assert "h24" in later["due"]
    assert "d7" in later["due"]
    assert "d30" not in later["due"]


def test_followup_requires_committed_decision(client):
    decision_id = client.post(
        "/api/v1/decisions", json={"input": INPUT_TEXT, "input_type": "text"}
    ).json()["decision_id"]
    client.post(f"/api/v1/decisions/{decision_id}/analyze")
    resp = client.post(
        f"/api/v1/decisions/{decision_id}/followups",
        json={"checkpoint": "h24", "executed": True},
    )
    assert resp.status_code == 409


def test_executed_followup_updates_posteriors_gradually(client):
    # 第一次决策 + 回访（executed=True）→ 形成 evidence_count=1 的后验
    first = _committed_case(client)
    client.post(
        f"/api/v1/decisions/{first}/followups",
        json={"checkpoint": "h24", "executed": True},
    )
    posteriors = client.get("/api/v1/profile/preferences").json()
    assert len(posteriors) >= 3
    assert all(p["evidence_count"] == 1 for p in posteriors)

    # 单次证据不影响新决策的初始权重（均匀分布）
    second = _committed_case(client)
    detail = client.get(f"/api/v1/decisions/{second}").json()
    initials = {c["name"]: c["initial_weight"] for c in detail["criteria"]}
    assert len(set(initials.values())) == 1  # 仍为均匀

    # 第二次回访后 evidence_count=2 → 开始影响第三个同类决策
    client.post(
        f"/api/v1/decisions/{second}/followups",
        json={"checkpoint": "h24", "executed": True},
    )
    posteriors = {
        p["criterion_name"]: p for p in client.get("/api/v1/profile/preferences").json()
    }
    assert posteriors["价格"]["evidence_count"] == 2

    third_id = client.post(
        "/api/v1/decisions", json={"input": INPUT_TEXT, "input_type": "text"}
    ).json()["decision_id"]
    client.post(f"/api/v1/decisions/{third_id}/analyze")
    client.post(f"/api/v1/decisions/{third_id}/advance")
    client.post(f"/api/v1/decisions/{third_id}/advance")  # 生成标准
    detail = client.get(f"/api/v1/decisions/{third_id}").json()
    initials = {c["name"]: c["initial_weight"] for c in detail["criteria"]}
    # 价格在前两次都被强烈偏好 → 第三次初始权重高于均匀值
    uniform = 1.0 / len(initials)
    assert initials["价格"] > uniform + 0.01


def test_unexecuted_decision_does_not_update_posteriors(client):
    decision_id = _committed_case(client)
    client.post(
        f"/api/v1/decisions/{decision_id}/followups",
        json={"checkpoint": "h24", "executed": False, "notes": "还没买"},
    )
    assert client.get("/api/v1/profile/preferences").json() == []


def test_user_can_delete_preference_profile(client):
    decision_id = _committed_case(client)
    client.post(
        f"/api/v1/decisions/{decision_id}/followups",
        json={"checkpoint": "h24", "executed": True},
    )
    assert len(client.get("/api/v1/profile/preferences").json()) > 0
    resp = client.delete("/api/v1/profile/preferences")
    assert resp.status_code == 200
    assert resp.json()["deleted"] > 0
    assert client.get("/api/v1/profile/preferences").json() == []
