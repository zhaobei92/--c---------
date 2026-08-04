"""P0-6 退款后免费重试漏洞的测试(审查发现)。

商业规则:
  * 系统自动重试(worker 内部 ≤3 次)不重复收费;
  * 任务终态失败 → 已扣分钟冲正(退款);
  * 已退款的终态任务,人工 retry 必须重新扣费(余额不足则 ENT_3001 拒绝);
  * 每次人工重试形成新的 charge generation,与原任务关联(retry_generation)。
"""

import hashlib

import pytest
from fastapi.testclient import TestClient

from app.api.deps import state
from app.main import app
from app.services.job_state_machine import JobStatus
from app.workers import pipeline
from app.workers.pipeline import StageError, run_once

client = TestClient(app)


@pytest.fixture(autouse=True)
def fresh_state():
    state.reset()
    yield


def _login(email="a@test.com"):
    code = client.post("/v1/auth/email/code", json={"email": email}).json()["dev_code"]
    tokens = client.post("/v1/auth/email/verify", json={"email": email, "code": code}).json()
    return {"Authorization": f"Bearer {tokens['access_token']}"}


def _make_job(headers, minutes=5):
    data = f"audio-{minutes}".encode()
    rec = client.post("/v1/recordings", json={
        "title": "rec", "duration_ms": minutes * 60000,
        "sha256": hashlib.sha256(data).hexdigest(), "size_bytes": len(data),
    }, headers=headers).json()
    return client.post("/v1/jobs", json={"recording_id": rec["id"]}, headers=headers).json()


@pytest.fixture
def force_failure():
    original = pipeline.PIPELINE[1]

    def always_fail(job, app_state):
        raise StageError("JOB_4101", "provider down")

    pipeline.PIPELINE[1] = (JobStatus.TRANSCRIBING, always_fail)
    yield
    pipeline.PIPELINE[1] = original


def _balance(headers):
    return client.get("/v1/entitlements/me", headers=headers).json()["total_minutes"]


def test_terminal_failure_refunds_minutes(force_failure):
    h = _login()
    start = _balance(h)
    job = _make_job(h)
    assert _balance(h) == start - 5  # 创建即扣
    run_once(state)
    assert client.get(f"/v1/jobs/{job['id']}", headers=h).json()["status"] == "failed"
    assert _balance(h) == start  # 终态失败已冲正


def test_retry_after_refund_recharges(force_failure):
    """核心漏洞用例:退款后的人工重试不得免费。"""
    h = _login()
    start = _balance(h)
    job = _make_job(h)
    run_once(state)
    assert _balance(h) == start  # 已退款

    # 人工重试:必须重新扣 5 分钟
    retried = client.post(f"/v1/jobs/{job['id']}/retry", headers=h).json()
    assert retried["status"] == "waiting"
    assert retried.get("retry_generation", 0) >= 1
    assert _balance(h) == start - 5

    # 再次失败 → 再次冲正;每一代扣费都可独立冲正
    run_once(state)
    assert _balance(h) == start


def test_repeated_retry_cannot_be_free(force_failure):
    """反复"失败→退款→重试"循环中,每一代重试都要重新扣费。"""
    h = _login()
    start = _balance(h)
    job = _make_job(h)
    run_once(state)
    for gen in (1, 2, 3):
        r = client.post(f"/v1/jobs/{job['id']}/retry", headers=h).json()
        assert r["retry_generation"] == gen
        assert _balance(h) == start - 5  # 本代已扣
        run_once(state)
        assert _balance(h) == start      # 本代已冲正


def test_retry_rejected_when_balance_insufficient(force_failure):
    h = _login()
    # 花掉几乎全部余额:创建一个大任务占用后失败退款,再用小额验证
    big = _make_job(h, minutes=118)  # 免费额度 120,剩 2
    assert big["minutes_charged"] == 118
    run_once(state)  # big 失败退款,余额回 120

    small = _make_job(h, minutes=119)
    run_once(state)  # 失败,退款
    # 消耗余额至不足:兑换外另建占位任务把余额吃掉
    filler = _make_job(h, minutes=115)
    assert filler["minutes_charged"] == 115
    # 此时余额 5,重试 small(需 119)必须拒绝
    r = client.post(f"/v1/jobs/{small['id']}/retry", headers=h)
    assert r.status_code == 402
    assert r.json()["error"]["code"] == "ENT_3001"


def test_ledger_traceability_across_generations(force_failure):
    """每代扣费/冲正在流水中可追溯到同一原始 job。"""
    h = _login()
    job = _make_job(h)
    run_once(state)
    client.post(f"/v1/jobs/{job['id']}/retry", headers=h)
    run_once(state)

    entries = client.get("/v1/usage", headers=h).json()["items"]
    consumes = [e for e in entries if e["reason"] == "consume"]
    refunds = [e for e in entries if e["reason"] == "refund"]
    assert len(consumes) == 2 and len(refunds) == 2
    for e in consumes + refunds:
        assert e["job_id"].startswith(job["id"])  # generation 后缀仍关联原任务
