"""P0-10 收尾:验证码链路必须具备 Hash 存储、有效期、频控与尝试上限。

多实例共享(Redis)在生产接入;本套测试锁定单实例行为契约。
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


def _send(email="a@test.com"):
    return client.post("/v1/auth/email/code", json={"email": email})


def test_code_stored_hashed_not_plaintext():
    resp = _send().json()
    raw = resp["dev_code"]
    entry = state.email_codes["a@test.com"]
    assert entry["code_hash"] != raw
    assert entry["code_hash"] == hashlib.sha256(raw.encode()).hexdigest()


def test_expired_code_rejected():
    resp = _send().json()
    state.email_codes["a@test.com"]["expires_at"] = 0  # 强制过期
    r = client.post("/v1/auth/email/verify",
                    json={"email": "a@test.com", "code": resp["dev_code"]})
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "AUTH_0001"


def test_resend_rate_limited():
    assert _send().status_code == 200
    r = _send()  # 60 秒冷却内重发
    assert r.status_code == 429
    assert r.json()["error"]["code"] == "AUTH_0002"


def test_attempts_exhausted_invalidates_code():
    resp = _send().json()
    for _ in range(5):
        r = client.post("/v1/auth/email/verify",
                        json={"email": "a@test.com", "code": "000000"})
        assert r.json()["error"]["code"] == "AUTH_0001"
    # 第 6 次:即使验证码正确也拒绝(AUTH_0008 尝试超限)
    r = client.post("/v1/auth/email/verify",
                    json={"email": "a@test.com", "code": resp["dev_code"]})
    assert r.status_code == 429
    assert r.json()["error"]["code"] == "AUTH_0008"
