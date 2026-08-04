"""P0-5 收尾:Outbox 投递必须"先投后删",入队失败不得丢事件。"""

import hashlib

import pytest
from fastapi.testclient import TestClient

from app.api.deps import state
from app.main import app
from app.workers.pipeline import publish_outbox

client = TestClient(app)


@pytest.fixture(autouse=True)
def fresh_state():
    state.reset()
    yield


class FlakyQueue:
    """第一次 enqueue 抛错,之后正常。"""

    def __init__(self, inner):
        self.inner = inner
        self.failures_left = 1

    def enqueue(self, topic, payload):
        if self.failures_left > 0:
            self.failures_left -= 1
            raise ConnectionError("queue down")
        self.inner.enqueue(topic, payload)

    def dequeue(self, topic):
        return self.inner.dequeue(topic)

    def nack(self, message):
        self.inner.nack(message)


def _login(email="a@test.com"):
    code = client.post("/v1/auth/email/code", json={"email": email}).json()["dev_code"]
    tokens = client.post("/v1/auth/email/verify", json={"email": email, "code": code}).json()
    return {"Authorization": f"Bearer {tokens['access_token']}"}


def test_enqueue_failure_keeps_event_in_outbox():
    h = _login()
    data = b"outbox-audio"
    rec = client.post("/v1/recordings", json={
        "title": "o", "duration_ms": 60000,
        "sha256": hashlib.sha256(data).hexdigest(), "size_bytes": len(data),
    }, headers=h).json()
    client.post("/v1/jobs", json={"recording_id": rec["id"]}, headers=h)
    assert len(state.outbox) == 1

    original_queue = state.queue
    state.queue = FlakyQueue(original_queue)
    try:
        published = publish_outbox(state)
        # 投递失败:事件保留,记录尝试信息,不静默丢失
        assert published == 0
        assert len(state.outbox) == 1
        assert state.outbox[0]["attempts"] == 1
        assert "queue down" in state.outbox[0]["last_error"]

        # 队列恢复:同一事件成功投递且只投一次
        assert publish_outbox(state) == 1
        assert state.outbox == []
    finally:
        state.queue = original_queue
