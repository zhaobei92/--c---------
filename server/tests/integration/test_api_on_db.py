"""API 层端到端集成测试:FastAPI 路由跑在 PostgreSQL + 真实 S3 后端上。

退出标准对应:核心链路不再依赖 state.users/state.jobs/state.recordings
内存字典;API"重启"(重建后端接线)后数据仍在。
流程:登录 → 登记录音 → init 上传(PG 会话 + 预签名)→ 真实 PUT 分片
→ complete → 建任务(事务扣费)→ Outbox 投递 → Worker 消费 → 通知可见。
"""

import hashlib

import pytest
import requests
from fastapi.testclient import TestClient

pytestmark = pytest.mark.integration

PART = 5 * 1024 * 1024
FREE = 120


@pytest.fixture
def api(db, s3_client):
    """TestClient + DB 后端接线;测试结束恢复内存模式。"""
    from app.api.deps import state
    from app.main import app
    from app.services.s3_store import S3ObjectStore
    from tests.integration.conftest import S3_ENDPOINT, S3_KEY, S3_SECRET

    state.reset()
    store = S3ObjectStore(endpoint=S3_ENDPOINT, access_key=S3_KEY,
                          secret_key=S3_SECRET, bucket="ysnote-audio")
    store.ensure_bucket()
    state.configure_db(db, store)
    yield TestClient(app)
    state.reset()


def _login(client, email="a@test.com"):
    code = client.post("/v1/auth/email/code", json={"email": email}).json()["dev_code"]
    tokens = client.post("/v1/auth/email/verify",
                         json={"email": email, "code": code}).json()
    return {"Authorization": f"Bearer {tokens['access_token']}"}


def test_full_api_flow_on_postgres(db, api):
    from app.api.deps import state
    from app.db.engine import make_session_factory
    from app.services.redis_queue import RedisStreamsQueue
    from app.workers.db_worker import run_worker_once
    from app.workers.outbox_publisher import publish_pending

    h = _login(api)
    # 内存字典必须保持为空:核心链路不再依赖它们
    assert state.users == {} and state.recordings == {} and state.jobs == {}

    data = bytes((i * 17) % 251 for i in range(PART + 4096))  # 2 片
    sha = hashlib.sha256(data).hexdigest()
    rec = api.post("/v1/recordings", json={
        "title": "meeting", "duration_ms": 5 * 60000,
        "sha256": sha, "size_bytes": len(data),
    }, headers=h).json()
    assert rec["deduplicated"] is False

    init = api.post("/v1/uploads/init", json={
        "recording_id": rec["id"], "size_bytes": len(data), "sha256": sha,
        "part_size": PART,
    }, headers=h).json()
    assert init["deduplicated"] is False and init["total_parts"] == 2

    for p in init["parts"]:
        n = p["part_no"]
        chunk = data[(n - 1) * PART: n * PART]
        put = requests.put(p["put_url"], data=chunk, timeout=30)
        etag = put.headers["ETag"].strip('"')
        r = api.post(f"/v1/uploads/{init['upload_id']}/parts/{n}/complete",
                     json={"etag": etag, "size_bytes": len(chunk)}, headers=h)
        assert r.status_code == 200
    done = api.post(f"/v1/uploads/{init['upload_id']}/complete", headers=h).json()
    assert done["completed"] is True

    job = api.post("/v1/jobs", json={"recording_id": rec["id"]}, headers=h).json()
    assert job["status"] == "waiting" and job["minutes_charged"] == 5
    assert api.get("/v1/entitlements/me", headers=h).json()["total_minutes"] == FREE - 5

    # Outbox → Redis Streams → Worker(与 API 同一 PG)
    import redis as redis_lib
    from tests.integration.conftest import REDIS_URL
    r = redis_lib.Redis.from_url(REDIS_URL, decode_responses=True)
    r.flushdb()
    queue = RedisStreamsQueue(r)
    sf = make_session_factory(db)
    assert publish_pending(sf, queue) == 1
    assert run_worker_once(sf, queue)["completed"] == 1

    assert api.get(f"/v1/jobs/{job['id']}", headers=h).json()["status"] == "completed"
    notes = api.get("/v1/notifications", headers=h).json()["items"]
    assert any(n["type"] == "job_completed" for n in notes)

    # 同录音重复建任务 → 幂等复用,不再扣费
    dup = api.post("/v1/jobs", json={"recording_id": rec["id"]}, headers=h).json()
    assert dup["deduplicated"] is True
    assert api.get("/v1/entitlements/me", headers=h).json()["total_minutes"] == FREE - 5


def test_api_restart_keeps_state(db, api, s3_client):
    """重建后端接线(= API 重启)后,用户/录音/余额全部还在。"""
    from app.api.deps import state
    from app.services.s3_store import S3ObjectStore
    from tests.integration.conftest import S3_ENDPOINT, S3_KEY, S3_SECRET

    h = _login(api, "restart@test.com")
    data = b"persistent-audio" * 10
    rec = api.post("/v1/recordings", json={
        "title": "keep", "duration_ms": 60000,
        "sha256": hashlib.sha256(data).hexdigest(), "size_bytes": len(data),
    }, headers=h).json()

    # 重启:reset 清空一切内存态,重新 configure_db(同一 PG)
    state.reset()
    store = S3ObjectStore(endpoint=S3_ENDPOINT, access_key=S3_KEY,
                          secret_key=S3_SECRET, bucket="ysnote-audio")
    state.configure_db(db, store)

    assert api.get(f"/v1/recordings/{rec['id']}", headers=h).status_code == 200
    assert api.get("/v1/entitlements/me", headers=h).json()["total_minutes"] == FREE


def test_cross_user_isolation_via_api(db, api):
    h1, h2 = _login(api, "u1@test.com"), _login(api, "u2@test.com")
    data = b"private-audio"
    rec = api.post("/v1/recordings", json={
        "title": "private", "duration_ms": 60000,
        "sha256": hashlib.sha256(data).hexdigest(), "size_bytes": len(data),
    }, headers=h1).json()
    assert api.get(f"/v1/recordings/{rec['id']}", headers=h2).status_code == 404
    r = api.post("/v1/uploads/init", json={
        "recording_id": rec["id"], "size_bytes": len(data),
        "sha256": hashlib.sha256(data).hexdigest(),
    }, headers=h2)
    assert r.status_code == 404
