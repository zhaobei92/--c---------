"""P0-3 越权修复的负向测试(审查发现:/uploads/init 未校验录音归属)。

要求:
  * 用户 B 不能用用户 A 的 recording_id 建上传会话(404,不泄露资源存在性);
  * 不存在 / 已删除的 recording 拒绝;
  * size_bytes / sha256 与录音登记元数据不一致拒绝(SYS_9004);
  * complete 后只允许写回属主自己的 recording。
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


def _login(email):
    code = client.post("/v1/auth/email/code", json={"email": email}).json()["dev_code"]
    tokens = client.post("/v1/auth/email/verify", json={"email": email, "code": code}).json()
    return {"Authorization": f"Bearer {tokens['access_token']}"}


def _make_recording(headers, data=b"audio-bytes" * 100):
    sha = hashlib.sha256(data).hexdigest()
    rec = client.post("/v1/recordings", json={
        "title": "rec", "duration_ms": 60000,
        "sha256": sha, "size_bytes": len(data),
    }, headers=headers).json()
    return rec, sha, len(data)


def test_cross_user_upload_init_rejected():
    ha, hb = _login("a@test.com"), _login("b@test.com")
    rec, sha, size = _make_recording(ha)
    r = client.post("/v1/uploads/init", json={
        "recording_id": rec["id"], "size_bytes": size, "sha256": sha,
    }, headers=hb)
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "DOC_6003"


def test_unknown_recording_rejected():
    ha = _login("a@test.com")
    r = client.post("/v1/uploads/init", json={
        "recording_id": "no-such-id", "size_bytes": 10, "sha256": "a" * 64,
    }, headers=ha)
    assert r.status_code == 404


def test_deleted_recording_rejected():
    ha = _login("a@test.com")
    rec, sha, size = _make_recording(ha)
    client.delete(f"/v1/recordings/{rec['id']}", headers=ha)
    r = client.post("/v1/uploads/init", json={
        "recording_id": rec["id"], "size_bytes": size, "sha256": sha,
    }, headers=ha)
    assert r.status_code == 404


def test_metadata_mismatch_rejected():
    ha = _login("a@test.com")
    rec, sha, size = _make_recording(ha)
    # sha 不一致
    r = client.post("/v1/uploads/init", json={
        "recording_id": rec["id"], "size_bytes": size, "sha256": "f" * 64,
    }, headers=ha)
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "SYS_9004"
    # size 不一致
    r = client.post("/v1/uploads/init", json={
        "recording_id": rec["id"], "size_bytes": size + 1, "sha256": sha,
    }, headers=ha)
    assert r.status_code == 422


def test_dedup_is_per_user_not_global():
    """P0-4:sha256 去重不得跨用户(防音频存在性探测/资产 ID 泄露)。"""
    ha, hb = _login("a@test.com"), _login("b@test.com")
    data = b"identical-audio" * 50
    sha = hashlib.sha256(data).hexdigest()

    rec_a, _, _ = _make_recording(ha, data)
    init_a = client.post("/v1/uploads/init", json={
        "recording_id": rec_a["id"], "size_bytes": len(data), "sha256": sha,
        "part_size": 4096,
    }, headers=ha).json()
    up = init_a["upload_id"]
    session = state.uploads.sessions[up]
    for p in init_a["parts"]:
        n = p["part_no"]
        chunk = data[(n - 1) * 4096: n * 4096]
        etag = state.object_store.put_part(session.storage_key, n, chunk)
        client.post(f"/v1/uploads/{up}/parts/{n}/complete",
                    json={"etag": etag, "size_bytes": len(chunk)}, headers=ha)
    assert client.post(f"/v1/uploads/{up}/complete", headers=ha).json()["completed"]

    # 用户 B 上传同一份内容:不得直接命中 A 的资产
    rec_b, _, _ = _make_recording(hb, data)
    init_b = client.post("/v1/uploads/init", json={
        "recording_id": rec_b["id"], "size_bytes": len(data), "sha256": sha,
    }, headers=hb).json()
    assert init_b["deduplicated"] is False

    # 同一用户重复上传同一内容:仍去重(不重复收费)
    rec_a2 = client.post("/v1/recordings", json={
        "title": "copy", "duration_ms": 60000, "sha256": sha, "size_bytes": len(data),
    }, headers=ha).json()
    assert rec_a2["deduplicated"] is True  # recordings 层已判重,返回同一条
    init_a2 = client.post("/v1/uploads/init", json={
        "recording_id": rec_a2["id"], "size_bytes": len(data), "sha256": sha,
    }, headers=ha).json()
    assert init_a2["deduplicated"] is True
