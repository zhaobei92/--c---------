"""Intake 流水线 + 编辑 + 审计的集成测试（LLM 未配置 → 启发式降级）。"""

INPUT_TEXT = "我在雷鸟U8和三星S27B800之间纠结，预算不超过5000元，主要用来办公。"


def _create(client, text=INPUT_TEXT):
    resp = client.post("/api/v1/decisions", json={"input": text, "input_type": "text"})
    assert resp.status_code == 201
    return resp.json()["decision_id"]


def test_analyze_runs_pipeline_with_heuristic_fallback(client):
    decision_id = _create(client)

    resp = client.post(f"/api/v1/decisions/{decision_id}/analyze")
    assert resp.status_code == 200
    detail = resp.json()

    # 低风险 → 走完风险分级并推进到 PROBLEM_NORMALIZATION
    assert detail["risk_level"] == "LOW"
    assert detail["status"] == "PROBLEM_NORMALIZATION"
    # 启发式提取出两个选项和一个硬约束
    assert [o["name"] for o in detail["options"]] == ["雷鸟U8", "三星S27B800"]
    assert len(detail["constraints"]) == 1
    assert detail["constraints"][0]["is_hard"] is True
    # 助手总结消息已追加
    roles = [m["role"] for m in detail["messages"]]
    assert roles[-1] == "assistant"


def test_analyze_is_idempotent(client):
    decision_id = _create(client)
    client.post(f"/api/v1/decisions/{decision_id}/analyze")
    first = client.get(f"/api/v1/decisions/{decision_id}").json()

    resp = client.post(f"/api/v1/decisions/{decision_id}/analyze")
    assert resp.status_code == 200
    second = resp.json()
    # 重复分析不产生重复选项和消息
    assert len(second["options"]) == len(first["options"])
    assert len(second["messages"]) == len(first["messages"])


def test_high_risk_goes_to_guided_only(client):
    decision_id = _create(client, "我在纠结要不要辞职，在留下和辞职之间摇摆。")
    resp = client.post(f"/api/v1/decisions/{decision_id}/analyze")
    detail = resp.json()
    assert detail["risk_level"] == "HIGH"
    assert detail["status"] == "GUIDED_ONLY"
    # 高风险不直接拍板：助手消息只提供梳理协助
    assert "不会替你直接拍板" in detail["messages"][-1]["content"]


def test_sse_stream_emits_events(client):
    decision_id = _create(client)
    events = []
    with client.stream("GET", f"/api/v1/decisions/{decision_id}/stream") as resp:
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("text/event-stream")
        for line in resp.iter_lines():
            if line.startswith("event: "):
                events.append(line.removeprefix("event: "))
    assert events[0] == "state"
    assert "risk" in events
    assert "extraction" in events
    assert "assistant" in events
    assert events[-1] == "done"


def test_option_edit_flow_with_audit(client):
    decision_id = _create(client)
    client.post(f"/api/v1/decisions/{decision_id}/analyze")
    detail = client.get(f"/api/v1/decisions/{decision_id}").json()
    option_id = detail["options"][0]["id"]

    # 修改 AI 识别错误
    resp = client.patch(
        f"/api/v1/decisions/{decision_id}/options/{option_id}",
        json={"name": "雷鸟U8 Pro"},
    )
    assert resp.status_code == 200
    assert resp.json()["name"] == "雷鸟U8 Pro"

    # 新增和删除
    resp = client.post(
        f"/api/v1/decisions/{decision_id}/options",
        json={"name": "LG C4", "description": "第三个候选"},
    )
    assert resp.status_code == 201
    new_id = resp.json()["id"]
    assert client.delete(f"/api/v1/decisions/{decision_id}/options/{new_id}").status_code == 204

    detail = client.get(f"/api/v1/decisions/{decision_id}").json()
    assert len(detail["options"]) == 2


def test_constraint_edit_flow(client):
    decision_id = _create(client)
    client.post(f"/api/v1/decisions/{decision_id}/analyze")

    resp = client.post(
        f"/api/v1/decisions/{decision_id}/constraints",
        json={"description": "必须支持Type-C供电", "is_hard": True},
    )
    assert resp.status_code == 201
    cid = resp.json()["id"]

    resp = client.patch(
        f"/api/v1/decisions/{decision_id}/constraints/{cid}",
        json={"is_hard": False},
    )
    assert resp.status_code == 200
    assert resp.json()["is_hard"] is False

    assert (
        client.delete(f"/api/v1/decisions/{decision_id}/constraints/{cid}").status_code
        == 204
    )


def test_llm_path_applies_structured_extraction(client):
    """网关配置可用时走 LLM 结构化提取（用 MockTransport 模拟供应商）。"""
    import json

    import httpx

    from app.gateway import get_model_gateway
    from model_gateway import LLMClient, ModelGateway, ModelGatewaySettings

    extraction = {
        "title": "主显示器选择",
        "domain": "product",
        "options": [
            {"name": "雷鸟U8", "description": "看片效果好"},
            {"name": "三星S27B800", "description": "办公舒服"},
        ],
        "facts": ["主要用途是办公"],
        "constraints": [{"description": "预算不超过5000元", "is_hard": True}],
        "concerns": ["怕看电影时遗憾"],
        "unknowns": ["影音场景实际占比"],
        "clarification_required": False,
    }

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": json.dumps(extraction)}}]},
        )

    settings = ModelGatewaySettings(
        MODEL_FAST="fake-fast-model",
        LLM_API_BASE_URL="https://llm.example.com/v1",
        LLM_API_KEY="sk-test",
    )
    gw = ModelGateway(
        settings, client=LLMClient(settings, transport=httpx.MockTransport(handler))
    )
    client.app.dependency_overrides[get_model_gateway] = lambda: gw
    try:
        decision_id = _create(client)
        detail = client.post(f"/api/v1/decisions/{decision_id}/analyze").json()
    finally:
        client.app.dependency_overrides.pop(get_model_gateway, None)

    assert detail["title"] == "主显示器选择"
    assert detail["domain"] == "product"
    assert [o["source"] for o in detail["options"]] == ["ai_extracted", "ai_extracted"]
    assert detail["facts"] == ["主要用途是办公"]
    assert detail["concerns"] == ["怕看电影时遗憾"]
    assert detail["constraints"][0]["is_hard"] is True
