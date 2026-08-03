"""阶段6：决策契约、锁定与 Reopen Gate 的集成测试。"""

INPUT_TEXT = "买显示器，我在雷鸟U8和三星S27B800之间纠结，预算不超过5000元，主要办公。"


def _prepare_ready(client):
    """走完全流程到 READY_TO_COMMIT。"""
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
    return decision_id, rec


def _commit(client, decision_id, option_id):
    return client.post(
        f"/api/v1/decisions/{decision_id}/commit",
        json={"selected_option_id": option_id, "accepted_tradeoffs": True},
    )


def test_commit_creates_contract_and_locks(client):
    decision_id, rec = _prepare_ready(client)
    resp = _commit(client, decision_id, rec["recommended_option_id"])
    assert resp.status_code == 201
    contract = resp.json()
    assert contract["user_confirmed"] is True
    assert contract["followed_recommendation"] is True
    assert len(contract["accepted_tradeoffs"]) >= 1
    assert len(contract["reopen_conditions"]) >= 1
    assert len(contract["non_reopen_conditions"]) >= 1

    detail = client.get(f"/api/v1/decisions/{decision_id}").json()
    assert detail["status"] == "COMMITTED"
    assert detail["selected_option_id"] == rec["recommended_option_id"]
    assert detail["committed_at"] is not None


def test_commit_requires_accepting_tradeoffs(client):
    decision_id, rec = _prepare_ready(client)
    resp = client.post(
        f"/api/v1/decisions/{decision_id}/commit",
        json={
            "selected_option_id": rec["recommended_option_id"],
            "accepted_tradeoffs": False,
        },
    )
    assert resp.status_code == 409


def test_user_may_choose_against_recommendation(client):
    """用户不被强迫接受推荐；契约如实记录未采纳。"""
    decision_id, rec = _prepare_ready(client)
    detail = client.get(f"/api/v1/decisions/{decision_id}").json()
    other = next(
        o["id"] for o in detail["options"] if o["id"] != rec["recommended_option_id"]
    )
    resp = _commit(client, decision_id, other)
    assert resp.status_code == 201
    assert resp.json()["followed_recommendation"] is False


def test_reopen_without_new_facts_is_rejected_and_full_flow_not_rerun(client):
    decision_id, rec = _prepare_ready(client)
    _commit(client, decision_id, rec["recommended_option_id"])
    runs_before = client.get(f"/api/v1/decisions/{decision_id}/runs/latest").json()["id"]

    resp = client.post(
        f"/api/v1/decisions/{decision_id}/reopen",
        json={"new_information": "我又开始后悔了，总觉得是不是选错了。"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["outcome"] == "rejected"
    assert body["reopen_score"] < 0.45
    # 拒绝时不许说"你不许后悔"——用规定的中性表达
    assert "不安" in body["message"]
    assert "不许" not in body["message"]
    # 决定保持锁定，完整流程没有重跑
    assert body["status"] == "COMMITTED"
    assert (
        client.get(f"/api/v1/decisions/{decision_id}/runs/latest").json()["id"]
        == runs_before
    )


def test_strong_new_fact_allows_full_reopen(client):
    decision_id, rec = _prepare_ready(client)
    _commit(client, decision_id, rec["recommended_option_id"])
    detail = client.get(f"/api/v1/decisions/{decision_id}").json()
    winner_name = next(
        o["name"] for o in detail["options"] if o["id"] == rec["recommended_option_id"]
    )
    crit_name = detail["criteria"][0]["name"]

    resp = client.post(
        f"/api/v1/decisions/{decision_id}/reopen",
        json={
            "new_information": (
                f"卖家刚承认这台{winner_name}是拆修过的翻新机，"
                f"官方检测报告显示{crit_name}实测数据比标称低30%，价格也涨了800元。"
            )
        },
    )
    body = resp.json()
    assert body["outcome"] == "full_reopen"
    assert body["reopen_score"] >= 0.70
    # 回到澄清阶段，可重新走流程
    assert body["status"] == "MORE_CLARIFICATION"
    adv = client.post(f"/api/v1/decisions/{decision_id}/advance").json()
    assert adv["status"] == "EVIDENCE_GAP_ANALYSIS"


def test_repeated_rumination_triggers_closure_intervention(client):
    decision_id, rec = _prepare_ready(client)
    _commit(client, decision_id, rec["recommended_option_id"])

    complaints = [
        "我又开始后悔了，总觉得是不是选错了。",
        "还是很后悔，觉得自己选错了。",
        "又后悔了，感觉真的选错了。",
    ]
    outcomes = []
    for text in complaints:
        body = client.post(
            f"/api/v1/decisions/{decision_id}/reopen", json={"new_information": text}
        ).json()
        outcomes.append(body["outcome"])
    assert outcomes[0] == "rejected"
    assert "closure_intervention" in outcomes
    final = [o for o in outcomes if o == "closure_intervention"]
    assert final  # 反刍干预被触发且有记录


def test_reopen_requires_committed_state(client):
    decision_id, _ = _prepare_ready(client)
    resp = client.post(
        f"/api/v1/decisions/{decision_id}/reopen",
        json={"new_information": "有新情况。"},
    )
    assert resp.status_code == 409


def test_commit_rejects_ineligible_option(client):
    decision_id, rec = _prepare_ready(client)
    detail = client.get(f"/api/v1/decisions/{decision_id}").json()
    other = next(
        o["id"] for o in detail["options"] if o["id"] != rec["recommended_option_id"]
    )
    client.patch(
        f"/api/v1/decisions/{decision_id}/options/{other}",
        json={"is_eligible": False, "elimination_reason": "超出预算"},
    )
    resp = _commit(client, decision_id, other)
    assert resp.status_code == 409
