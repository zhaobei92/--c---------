"""Demo Mode 黄金流程(服务端段)集成测试:

登记录音 → 建任务 → Outbox → Worker(Mock AI Provider)→
speakers/transcript_segments/summaries 真实落库 →
API 读转写与摘要 → 摘要每条待办 100% 携带可定位的时间戳证据 →
说话人改名后全篇一致 → 重复投递不产生重复内容。
"""

import hashlib

import pytest
from fastapi.testclient import TestClient

pytestmark = pytest.mark.integration

FREE = 120


@pytest.fixture
def api(db, s3_client):
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


def _login(client, email="demo@test.com"):
    code = client.post("/v1/auth/email/code", json={"email": email}).json()["dev_code"]
    tokens = client.post("/v1/auth/email/verify",
                         json={"email": email, "code": code}).json()
    return {"Authorization": f"Bearer {tokens['access_token']}"}


def _run_pipeline(db):
    import redis as redis_lib
    from app.db.engine import make_session_factory
    from app.services.redis_queue import RedisStreamsQueue
    from app.workers.db_worker import run_worker_once, stages_for
    from app.workers.outbox_publisher import publish_pending
    from tests.integration.conftest import REDIS_URL

    r = redis_lib.Redis.from_url(REDIS_URL, decode_responses=True)
    r.flushdb()
    queue = RedisStreamsQueue(r)
    sf = make_session_factory(db)
    publish_pending(sf, queue)
    return run_worker_once(sf, queue, stages=stages_for("mock")), queue, sf


def test_demo_flow_transcript_summary_and_rename(db, api):
    h = _login(api)
    data = b"demo-meeting-audio" * 64
    rec = api.post("/v1/recordings", json={
        "title": "demo meeting", "duration_ms": 52000,
        "sha256": hashlib.sha256(data).hexdigest(), "size_bytes": len(data),
    }, headers=h).json()
    job = api.post("/v1/jobs", json={"recording_id": rec["id"]}, headers=h).json()

    stats, queue, sf = _run_pipeline(db)
    assert stats["completed"] == 1
    assert api.get(f"/v1/jobs/{job['id']}", headers=h).json()["status"] == "completed"

    # 转写:分段 + 说话人 + 时间戳单调
    transcript = api.get(f"/v1/recordings/{rec['id']}/transcript",
                         headers=h).json()["segments"]
    assert len(transcript) == 6
    assert [seg["seq"] for seg in transcript] == list(range(6))
    assert all(seg["end_ms"] > seg["start_ms"] for seg in transcript)
    assert {seg["speaker"] for seg in transcript} == {"Speaker 1", "Speaker 2"}
    assert any(seg["language"] == "en" for seg in transcript)  # 混语分段

    # 摘要:每条待办/结论 100% 携带证据,且证据可定位到真实分段
    summary = api.get(f"/v1/recordings/{rec['id']}/summary", headers=h).json()
    items = summary["items"]
    assert len(items) == 4
    segment_ids = {seg["segment_id"] for seg in transcript}
    for item in items:
        assert item["evidence"], f"条目缺少时间戳证据: {item['text']}"
        for ev in item["evidence"]:
            assert ev["segment_id"] in segment_ids
            assert ev["end_ms"] > ev["start_ms"]
    assert any(i["type"] == "todo" for i in items)

    # 说话人改名 → 全篇(转写读取)一致
    speakers = api.get(f"/v1/recordings/{rec['id']}/speakers", headers=h).json()["speakers"]
    sp1 = next(s for s in speakers if s["label"] == "Speaker 1")
    renamed = api.patch(f"/v1/recordings/{rec['id']}/speakers", json={
        "speaker_id": sp1["speaker_id"], "display_name": "王总",
    }, headers=h).json()["speakers"]
    assert next(s for s in renamed if s["label"] == "Speaker 1")["display_name"] == "王总"
    transcript2 = api.get(f"/v1/recordings/{rec['id']}/transcript",
                          headers=h).json()["segments"]
    assert {seg["speaker"] for seg in transcript2} == {"王总", "Speaker 2"}

    # 重复投递(至少一次语义):内容不重复
    from app.services.queue import TOPIC_TRANSCRIBE
    from app.workers.db_worker import run_worker_once, stages_for
    queue.publish(TOPIC_TRANSCRIBE, "dup", {"job_id": job["id"]})
    run_worker_once(sf, queue, stages=stages_for("mock"))
    again = api.get(f"/v1/recordings/{rec['id']}/transcript", headers=h).json()["segments"]
    assert len(again) == 6


def test_transcript_cross_user_rejected(db, api):
    h1, h2 = _login(api, "a@test.com"), _login(api, "b@test.com")
    data = b"private"
    rec = api.post("/v1/recordings", json={
        "title": "p", "duration_ms": 60000,
        "sha256": hashlib.sha256(data).hexdigest(), "size_bytes": len(data),
    }, headers=h1).json()
    for path in (f"/v1/recordings/{rec['id']}/transcript",
                 f"/v1/recordings/{rec['id']}/summary",
                 f"/v1/recordings/{rec['id']}/speakers"):
        assert api.get(path, headers=h2).status_code == 404
