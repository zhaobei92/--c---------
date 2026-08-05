"""端到端冒烟:注册 → 绑定设备 → 登记录音 → 分片上传 → 创建任务 → worker 处理 → 通知。

对应阶段3 退出标准:端到端流程贯通;同一文件不重复收费;失败可重试。
"""

import hashlib

import pytest
from fastapi.testclient import TestClient

from app.api.deps import state
from app.main import app
from app.workers.pipeline import run_once

client = TestClient(app)


@pytest.fixture(autouse=True)
def fresh_state():
    state.reset()
    yield


def _login(email="a@test.com") -> dict:
    code = client.post("/v1/auth/email/code", json={"email": email}).json()["dev_code"]
    tokens = client.post("/v1/auth/email/verify", json={"email": email, "code": code}).json()
    return {"Authorization": f"Bearer {tokens['access_token']}"}


def test_health_and_openapi():
    assert client.get("/health").json()["status"] == "ok"
    paths = client.get("/openapi.json").json()["paths"]
    for p in ("/v1/auth/email/verify", "/v1/uploads/init", "/v1/jobs",
              "/v1/entitlements/me", "/admin/costs"):
        assert p in paths


def test_wrong_code_rejected():
    client.post("/v1/auth/email/code", json={"email": "b@test.com"})
    r = client.post("/v1/auth/email/verify", json={"email": "b@test.com", "code": "000000"})
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "AUTH_0001"


def test_device_binding_conflict():
    h1, h2 = _login("u1@test.com"), _login("u2@test.com")
    body = {"sn": "SN001", "model": "MOCK-1"}
    assert client.post("/v1/devices/bind", json=body, headers=h1).status_code == 200
    r = client.post("/v1/devices/bind", json=body, headers=h2)
    assert r.status_code == 409
    assert r.json()["error"]["code"] == "DEV_1101"  # 验收红线:绑定串号 0


def test_full_flow_recording_to_completed_job():
    h = _login()
    data = b"fake-audio-bytes" * 1000
    sha = hashlib.sha256(data).hexdigest()

    rec = client.post("/v1/recordings", json={
        "title": "meeting", "duration_ms": 5 * 60000,
        "sha256": sha, "size_bytes": len(data),
    }, headers=h).json()
    assert rec["deduplicated"] is False

    # 同 sha256 再登记 → 去重
    again = client.post("/v1/recordings", json={
        "title": "meeting-copy", "duration_ms": 5 * 60000,
        "sha256": sha, "size_bytes": len(data),
    }, headers=h).json()
    assert again["deduplicated"] is True and again["id"] == rec["id"]

    init = client.post("/v1/uploads/init", json={
        "recording_id": rec["id"], "size_bytes": len(data),
        "sha256": sha, "part_size": 4096,
    }, headers=h).json()
    upload_id = init["upload_id"]
    session = state.uploads.sessions[upload_id]
    for p in init["parts"]:
        n = p["part_no"]
        chunk = data[(n - 1) * 4096: n * 4096]
        etag = state.object_store.put_part(session.storage_key, n, chunk)
        client.post(f"/v1/uploads/{upload_id}/parts/{n}/complete",
                    json={"etag": etag, "size_bytes": len(chunk)}, headers=h)
    done = client.post(f"/v1/uploads/{upload_id}/complete", headers=h).json()
    assert done["completed"] is True

    before = client.get("/v1/entitlements/me", headers=h).json()["total_minutes"]
    job = client.post("/v1/jobs", json={"recording_id": rec["id"]}, headers=h).json()
    assert job["status"] == "waiting" and job["minutes_charged"] == 5
    after = client.get("/v1/entitlements/me", headers=h).json()["total_minutes"]
    assert before - after == 5

    assert run_once(state) == 1
    job2 = client.get(f"/v1/jobs/{job['id']}", headers=h).json()
    assert job2["status"] == "completed"
    notes = client.get("/v1/notifications", headers=h).json()["items"]
    assert any(n["type"] == "job_completed" for n in notes)

    # 同 recording 再提交 → 复用,不再扣分钟
    dup = client.post("/v1/jobs", json={"recording_id": rec["id"]}, headers=h).json()
    assert dup["deduplicated"] is True
    assert client.get("/v1/entitlements/me", headers=h).json()["total_minutes"] == after


def test_insufficient_minutes_rejected():
    h = _login()
    rec = client.post("/v1/recordings", json={
        "title": "long", "duration_ms": 10_000 * 60000,  # 一万分钟
        "sha256": "f" * 64, "size_bytes": 1,
    }, headers=h).json()
    r = client.post("/v1/jobs", json={"recording_id": rec["id"]}, headers=h)
    assert r.status_code == 402
    assert r.json()["error"]["code"] == "ENT_3001"


def test_redeem_and_admin_endpoints():
    h = _login()
    assert client.post("/v1/redeem", json={"code": "WELCOME300"}, headers=h).json()[
        "granted_minutes"] == 300
    r = client.post("/v1/redeem", json={"code": "WELCOME300"}, headers=h)
    assert r.json()["error"]["code"] == "ENT_3006"

    assert client.get("/admin/costs").status_code == 403  # 无管理 token
    admin = {"X-Admin-Token": "dev-admin"}
    assert client.get("/admin/costs", headers=admin).status_code == 200
    assert client.get("/admin/users", headers=admin).json()["total"] == 1


def test_data_isolation_between_users():
    h1, h2 = _login("u1@test.com"), _login("u2@test.com")
    rec = client.post("/v1/recordings", json={
        "title": "private", "duration_ms": 60000, "sha256": "a" * 64, "size_bytes": 10,
    }, headers=h1).json()
    r = client.get(f"/v1/recordings/{rec['id']}", headers=h2)
    assert r.status_code == 404  # 数据隔离:不泄露他人资源
