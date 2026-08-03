"""端到端 Smoke Test：新建决策 → 查询详情 → 发送消息 → 列表可见。"""

INPUT_TEXT = (
    "我在雷鸟U8和三星S27B800之间纠结，三星办公舒服，雷鸟看片效果好，"
    "我平时主要工作，但是又怕偶尔看电影的时候遗憾。"
)


def test_decision_smoke_flow(client):
    # 1. 新建决策
    resp = client.post("/api/v1/decisions", json={"input": INPUT_TEXT, "input_type": "text"})
    assert resp.status_code == 201
    created = resp.json()
    decision_id = created["decision_id"]
    assert created["status"] == "INTAKE"
    assert created["stream_url"].endswith(f"/decisions/{decision_id}/stream")

    # 2. 查询详情：首条用户消息已入库
    resp = client.get(f"/api/v1/decisions/{decision_id}")
    assert resp.status_code == 200
    detail = resp.json()
    assert detail["status"] == "INTAKE"
    assert len(detail["messages"]) == 1
    assert detail["messages"][0]["role"] == "user"
    assert detail["messages"][0]["content"] == INPUT_TEXT

    # 3. 发送回答：用户消息 + 占位助手回复
    resp = client.post(
        f"/api/v1/decisions/{decision_id}/messages",
        json={"content": "影音大概占两成时间，预算差价能接受1000元以内。"},
    )
    assert resp.status_code == 201
    msg = resp.json()
    assert msg["user_message"]["role"] == "user"
    assert msg["assistant_message"]["role"] == "assistant"

    resp = client.get(f"/api/v1/decisions/{decision_id}")
    assert len(resp.json()["messages"]) == 3

    # 4. 决策出现在列表中
    resp = client.get("/api/v1/decisions")
    assert resp.status_code == 200
    ids = [c["id"] for c in resp.json()]
    assert decision_id in ids


def test_get_missing_decision_404(client):
    resp = client.get("/api/v1/decisions/00000000-0000-0000-0000-000000000000")
    assert resp.status_code == 404


def test_empty_input_rejected(client):
    resp = client.post("/api/v1/decisions", json={"input": "", "input_type": "text"})
    assert resp.status_code == 422
