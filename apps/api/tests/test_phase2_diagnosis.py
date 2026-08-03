"""阶段2：纠结诊断与追问的集成测试（LLM 未配置 → 启发式）。"""

INPUT_TEXT = (
    "我在雷鸟U8和三星S27B800之间纠结，主要工作用，"
    "但怕看电影的时候遗憾，已经翻来覆去想了好几天。"
)


def _prepare(client, text=INPUT_TEXT):
    resp = client.post("/api/v1/decisions", json={"input": text, "input_type": "text"})
    decision_id = resp.json()["decision_id"]
    client.post(f"/api/v1/decisions/{decision_id}/analyze")
    return decision_id


def test_advance_runs_diagnosis(client):
    decision_id = _prepare(client)
    resp = client.post(f"/api/v1/decisions/{decision_id}/advance")
    assert resp.status_code == 200
    body = resp.json()
    assert body["kind"] == "diagnosis"
    diagnosis = body["diagnosis"]
    # "怕…遗憾" 命中后悔恐惧，"翻来覆去/好几天" 命中反刍——两者都应出现
    assert "regret_aversion" in diagnosis["type_scores"]
    assert "rumination" in diagnosis["type_scores"]
    assert diagnosis["primary_type"] in ("regret_aversion", "rumination")
    # 系统分数来自确定性计算，不是模型自报
    assert 0 < diagnosis["information_completeness"] <= 1

    detail = client.get(f"/api/v1/decisions/{decision_id}").json()
    assert detail["primary_stuck_type"] == diagnosis["primary_type"]
    assert detail["status"] == "PREFERENCE_ELICITATION"


def test_one_question_per_advance_and_no_repeat(client):
    decision_id = _prepare(client)
    client.post(f"/api/v1/decisions/{decision_id}/advance")  # 诊断

    asked_targets = []
    for _ in range(10):  # 上限内反复推进
        body = client.post(f"/api/v1/decisions/{decision_id}/advance").json()
        if body["kind"] != "question":
            break
        target = body["question"]["target_variable"]
        # 不重复问同一目标
        assert target not in asked_targets
        asked_targets.append(target)
        # 每轮只有一个问题，且作为助手消息出现
        detail = client.get(f"/api/v1/decisions/{decision_id}").json()
        assert detail["messages"][-1]["content"] == body["question"]["question"]

    # 最终必须停下来（不会无限提问）
    assert body["kind"] in ("ready_to_compute", "evaluations_needed")
    assert len(asked_targets) <= 5


def test_answer_resolves_unknown_and_becomes_fact(client):
    decision_id = _prepare(client)
    client.post(f"/api/v1/decisions/{decision_id}/advance")

    body = client.post(f"/api/v1/decisions/{decision_id}/advance").json()
    assert body["kind"] == "question"
    target = body["question"]["target_variable"]

    client.post(
        f"/api/v1/decisions/{decision_id}/messages",
        json={"content": "看电影大概每周一两次，预算其实无所谓。"},
    )
    detail = client.get(f"/api/v1/decisions/{decision_id}").json()
    # 回答进入事实列表
    assert any(target in f for f in detail["facts"])
    # unknown 类问题回答后从未知列表消除
    if target.startswith("unknown:"):
        assert target.removeprefix("unknown:") not in detail["unknowns"]


def test_question_rounds_capped_at_five(client):
    decision_id = _prepare(client, "我在A和B之间纠结，不知道怎么选，很多都不了解。")
    client.post(f"/api/v1/decisions/{decision_id}/advance")
    question_count = 0
    for _ in range(12):
        body = client.post(f"/api/v1/decisions/{decision_id}/advance").json()
        if body["kind"] == "question":
            question_count += 1
        else:
            break
    assert question_count <= 5


def test_guided_only_decision_never_gets_diagnosed_verdict(client):
    resp = client.post(
        "/api/v1/decisions",
        json={"input": "我在纠结要不要辞职，在留下和辞职之间摇摆。", "input_type": "text"},
    )
    decision_id = resp.json()["decision_id"]
    client.post(f"/api/v1/decisions/{decision_id}/analyze")
    body = client.post(f"/api/v1/decisions/{decision_id}/advance").json()
    assert body["kind"] == "guided_only"


def test_advance_before_intake_is_noop(client):
    resp = client.post(
        "/api/v1/decisions", json={"input": "A和B之间纠结。", "input_type": "text"}
    )
    decision_id = resp.json()["decision_id"]
    body = client.post(f"/api/v1/decisions/{decision_id}/advance").json()
    assert body["kind"] == "noop"
