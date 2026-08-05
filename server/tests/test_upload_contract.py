"""客户端—服务端上传契约测试。

Dart 客户端(mobile/lib/data/api/api_client.dart)按此契约解析;本文件与其保持同步:
  * init 请求必填 recording_id / size_bytes / sha256(OpenAPI schema 断言);
  * init 响应必含 upload_id / part_size / parts[{part_no, put_url}];
  * 进度响应必含 pending_parts 且每个 pending 分片带可用 put_url(App 重启后
    凭此续传,预签名 URL 过期后重新签发);
  * 分片登记必填 etag / size_bytes。
"""

import hashlib

import pytest
from fastapi.testclient import TestClient

from app.api.deps import state
from app.main import app

client = TestClient(app)


@pytest.fixture(autouse=True)
def fresh_state():
    state.reset()
    yield


def _login(email="a@test.com"):
    code = client.post("/v1/auth/email/code", json={"email": email}).json()["dev_code"]
    tokens = client.post("/v1/auth/email/verify", json={"email": email, "code": code}).json()
    return {"Authorization": f"Bearer {tokens['access_token']}"}


def test_openapi_init_required_fields():
    spec = client.get("/openapi.json").json()
    body = spec["paths"]["/v1/uploads/init"]["post"]["requestBody"]
    schema_ref = body["content"]["application/json"]["schema"]["$ref"]
    schema = spec["components"]["schemas"][schema_ref.rsplit("/", 1)[-1]]
    assert set(schema["required"]) >= {"recording_id", "size_bytes", "sha256"}


def test_init_response_shape_matches_dart_client():
    h = _login()
    data = b"contract-audio" * 300
    sha = hashlib.sha256(data).hexdigest()
    rec = client.post("/v1/recordings", json={
        "title": "c", "duration_ms": 60000, "sha256": sha, "size_bytes": len(data),
    }, headers=h).json()
    init = client.post("/v1/uploads/init", json={
        "recording_id": rec["id"], "size_bytes": len(data), "sha256": sha,
        "part_size": 1024,
    }, headers=h).json()
    assert init["deduplicated"] is False
    assert isinstance(init["upload_id"], str)
    assert init["part_size"] == 1024
    assert len(init["parts"]) == (len(data) + 1023) // 1024
    for p in init["parts"]:
        assert isinstance(p["part_no"], int)
        assert p["put_url"].startswith("http")


def test_progress_reissues_put_urls_for_pending_parts():
    """断点续传契约:App 重启后 GET /uploads/{id} 拿到 pending 分片 + 新 put_url。"""
    h = _login()
    data = b"resume-audio" * 400
    sha = hashlib.sha256(data).hexdigest()
    rec = client.post("/v1/recordings", json={
        "title": "r", "duration_ms": 60000, "sha256": sha, "size_bytes": len(data),
    }, headers=h).json()
    init = client.post("/v1/uploads/init", json={
        "recording_id": rec["id"], "size_bytes": len(data), "sha256": sha,
        "part_size": 1024,
    }, headers=h).json()
    up = init["upload_id"]
    session = state.uploads.sessions[up]
    # 只传第 1 片,模拟中断
    chunk = data[:1024]
    etag = state.object_store.put_part(session.storage_key, 1, chunk)
    client.post(f"/v1/uploads/{up}/parts/1/complete",
                json={"etag": etag, "size_bytes": 1024}, headers=h)

    progress = client.get(f"/v1/uploads/{up}", headers=h).json()
    assert 1 not in progress["pending_parts"]
    assert progress["total_parts"] == len(init["parts"])
    # 每个待传分片都带续传用的 put_url
    assert {p["part_no"] for p in progress["parts"]} == set(progress["pending_parts"])
    for p in progress["parts"]:
        assert p["put_url"].startswith("http")


def test_part_complete_requires_etag_and_size():
    spec = client.get("/openapi.json").json()
    path = "/v1/uploads/{upload_id}/parts/{part_no}/complete"
    body = spec["paths"][path]["post"]["requestBody"]
    schema_ref = body["content"]["application/json"]["schema"]["$ref"]
    schema = spec["components"]["schemas"][schema_ref.rsplit("/", 1)[-1]]
    assert set(schema["required"]) >= {"etag", "size_bytes"}
