"""Outbox → Redis Streams → DB Worker 可靠消息集成测试。

覆盖:SKIP LOCKED 投递、失败退避保留、幂等消费、Worker 崩溃后 XAUTOCLAIM
重领、毒消息 DLQ、终态失败冲正、通知落库。
"""

import hashlib

import pytest
from sqlalchemy import select

pytestmark = pytest.mark.integration

FREE = 120


@pytest.fixture
def sf(db):
    from app.db.engine import make_session_factory
    return make_session_factory(db)


@pytest.fixture
def golden(sf):
    from app.db.golden import GoldenChainDb
    return GoldenChainDb(sf)


@pytest.fixture
def queue(redis_db):
    from app.services.redis_queue import RedisStreamsQueue
    return RedisStreamsQueue(redis_db)


def _job(golden, minutes=5, email="a@test.com"):
    user_id, _ = golden.ensure_user(email)
    data = f"audio-{minutes}-{email}".encode()
    rec = golden.create_recording(user_id, {
        "title": "rec", "duration_ms": minutes * 60000,
        "sha256": hashlib.sha256(data).hexdigest(), "size_bytes": len(data),
    })
    return user_id, golden.create_job(user_id, rec["id"])


def test_publish_pending_marks_and_is_idempotent(db, sf, golden, queue):
    from app.workers.outbox_publisher import publish_pending
    _job(golden)
    assert publish_pending(sf, queue) == 1
    assert publish_pending(sf, queue) == 0  # 已投递不重投

    from app.models.tables import OutboxEvent
    with sf() as s:
        row = s.execute(select(OutboxEvent)).scalar_one()
        assert row.published_at is not None
        assert row.attempts == 0


def test_publish_failure_keeps_row_with_backoff(db, sf, golden, queue):
    from app.workers.outbox_publisher import publish_pending

    class Broken:
        def publish(self, *a, **k):
            raise ConnectionError("redis down")

    _job(golden)
    assert publish_pending(sf, Broken()) == 0
    from app.models.tables import OutboxEvent
    with sf() as s:
        row = s.execute(select(OutboxEvent)).scalar_one()
        assert row.published_at is None
        assert row.attempts == 1
        assert "redis down" in row.last_error
        assert row.next_attempt_at is not None  # 退避后重投

    # 退避期内不重试;将 next_attempt_at 拉回后真实队列投递成功
    with sf() as s, s.begin():
        from datetime import datetime, timezone
        row = s.execute(select(OutboxEvent)).scalar_one()
        row.next_attempt_at = datetime.now(timezone.utc)
    assert publish_pending(sf, queue) == 1


def test_worker_completes_job_and_acks(db, sf, golden, queue):
    from app.models.tables import NotificationJob
    from app.services.queue import TOPIC_TRANSCRIBE
    from app.workers.db_worker import run_worker_once
    from app.workers.outbox_publisher import publish_pending

    user_id, job = _job(golden)
    publish_pending(sf, queue)
    stats = run_worker_once(sf, queue)
    assert stats["completed"] == 1
    assert golden.get_job(user_id, job["id"])["status"] == "completed"
    assert queue.pending_count(TOPIC_TRANSCRIBE) == 0  # 已 ACK
    with sf() as s:
        notes = s.execute(select(NotificationJob)).scalars().all()
        assert any(n.type == "job_completed" for n in notes)
    # 重复投递(至少一次语义):幂等跳过,不重复处理
    queue.publish(TOPIC_TRANSCRIBE, "dup-event", {"job_id": job["id"]})
    stats2 = run_worker_once(sf, queue)
    assert stats2["skipped"] == 1 and stats2["completed"] == 0


def test_worker_crash_recovery_via_autoclaim(db, sf, golden, queue):
    """Worker 读到消息后崩溃(未 ACK)→ 另一 Worker XAUTOCLAIM 重领完成。"""
    from app.services.queue import TOPIC_TRANSCRIBE
    from app.workers.db_worker import run_worker_once
    from app.workers.outbox_publisher import publish_pending

    user_id, job = _job(golden)
    publish_pending(sf, queue)
    crashed = queue.read(TOPIC_TRANSCRIBE, consumer="w-crash", count=10)
    assert len(crashed) == 1  # w-crash 拿到消息后"崩溃":不处理不 ACK
    assert queue.pending_count(TOPIC_TRANSCRIBE) == 1

    stats = run_worker_once(sf, queue, consumer="w2", claim_idle_ms=0)
    assert stats["completed"] == 1
    assert golden.get_job(user_id, job["id"])["status"] == "completed"
    assert queue.pending_count(TOPIC_TRANSCRIBE) == 0


def test_failed_job_refunds_and_notifies(db, sf, golden, queue):
    from app.services.job_state_machine import JobStatus
    from app.workers.db_worker import run_worker_once
    from app.workers.outbox_publisher import publish_pending
    from app.workers.pipeline import StageError

    def always_fail(s, job):
        raise StageError("JOB_4101", "provider down")

    stages = [(JobStatus.PREPROCESSING, lambda s, j: None),
              (JobStatus.TRANSCRIBING, always_fail),
              (JobStatus.DIARIZING, lambda s, j: None),
              (JobStatus.SUMMARIZING, lambda s, j: None)]

    user_id, job = _job(golden, minutes=9)
    assert golden.balances(user_id)["free_monthly"] == FREE - 9
    publish_pending(sf, queue)
    stats = run_worker_once(sf, queue, stages=stages)
    assert stats["failed"] == 1
    view = golden.get_job(user_id, job["id"])
    assert view["status"] == "failed" and view["error_code"] == "JOB_4201"
    assert golden.balances(user_id)["free_monthly"] == FREE  # 已冲正


def test_poison_message_goes_to_dlq(db, sf, queue):
    from app.services.queue import TOPIC_TRANSCRIBE
    from app.workers.db_worker import MAX_DELIVERIES, run_worker_once

    queue.publish(TOPIC_TRANSCRIBE, "poison", {"job_id": "00000000-0000-0000-0000-000000000000"})
    for _ in range(MAX_DELIVERIES + 1):
        stats = run_worker_once(sf, queue, claim_idle_ms=0)
        if stats["dlq"]:
            break
    assert queue.dlq_length(TOPIC_TRANSCRIBE) == 1
    assert queue.pending_count(TOPIC_TRANSCRIBE) == 0  # 原消息已 ACK
