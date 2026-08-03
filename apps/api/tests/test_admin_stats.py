"""管理统计与模型健康检查（阶段8）。"""


def test_model_health_reports_configuration(client):
    resp = client.get("/api/v1/admin/health/models")
    assert resp.status_code == 200
    body = resp.json()
    assert "credentials_configured" in body
    assert set(body["roles"].keys()) == {
        "fast",
        "default",
        "deep",
        "challenger",
        "embedding",
    }
    # 响应中不得泄露密钥
    assert "sk-" not in resp.text


def test_stats_shapes_and_counts(client):
    # 造一条决策数据
    decision_id = client.post(
        "/api/v1/decisions",
        json={"input": "我在A方案和B方案之间纠结。", "input_type": "text"},
    ).json()["decision_id"]
    client.post(f"/api/v1/decisions/{decision_id}/analyze")

    resp = client.get("/api/v1/admin/stats")
    assert resp.status_code == 200
    stats = resp.json()
    assert stats["decisions"]["total"] >= 1
    assert isinstance(stats["decisions"]["by_status"], dict)
    assert "meaningful_reopen_rate" in stats["reopens"]
    assert "action_rate" in stats["followups"]
    assert stats["model_invocations"]["total"] >= 1  # intake 的失败调用也被记录
