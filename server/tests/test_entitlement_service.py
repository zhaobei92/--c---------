from datetime import datetime, timedelta, timezone

import pytest

from app.services.entitlement_service import (
    DuplicateOperation, EntitlementService, InMemoryLedgerStore, InsufficientMinutes,
)

NOW = datetime(2026, 8, 3, tzinfo=timezone.utc)
U = "user-1"


@pytest.fixture
def svc():
    return EntitlementService(InMemoryLedgerStore(), now=lambda: NOW)


def test_grant_and_balance(svc):
    svc.grant(U, "free_monthly", 120, source_type="system")
    assert svc.available_minutes(U) == 120
    assert svc.bucket_balances(U)["free_monthly"] == 120


def test_deduction_order_across_buckets(svc):
    # 铁律:free_monthly → gift → member → purchased
    svc.grant(U, "purchased", 100, source_type="order")
    svc.grant(U, "member", 100, source_type="order")
    svc.grant(U, "gift", 30, source_type="redeem")
    svc.grant(U, "free_monthly", 50, source_type="system")
    svc.consume(U, 90, job_id="job-1")  # 50 free + 30 gift + 10 member
    b = svc.bucket_balances(U)
    assert b["free_monthly"] == 0
    assert b["gift"] == 0
    assert b["member"] == 90
    assert b["purchased"] == 100


def test_same_bucket_earlier_expiry_first(svc):
    late = svc.grant(U, "purchased", 100, source_type="order",
                     expires_at=NOW + timedelta(days=300))
    early = svc.grant(U, "purchased", 100, source_type="order",
                      expires_at=NOW + timedelta(days=30))
    svc.consume(U, 120, job_id="job-1")
    per_ent = {}
    for e in svc.store.entries_for(U):
        per_ent[e.entitlement_id] = per_ent.get(e.entitlement_id, 0) + e.delta_minutes
    assert per_ent[early.id] == 0     # 先到期先扣光
    assert per_ent[late.id] == 80


def test_insufficient_minutes(svc):
    svc.grant(U, "free_monthly", 10, source_type="system")
    with pytest.raises(InsufficientMinutes) as e:
        svc.consume(U, 60, job_id="job-1")
    assert e.value.required == 60
    assert e.value.available == 10
    # 失败的扣减不落任何流水
    assert svc.available_minutes(U) == 10


def test_consume_idempotent_per_job(svc):
    # 验收红线:重复扣费 = 0
    svc.grant(U, "member", 100, source_type="order")
    svc.consume(U, 30, job_id="job-1")
    with pytest.raises(DuplicateOperation):
        svc.consume(U, 30, job_id="job-1")
    assert svc.available_minutes(U) == 70


def test_refund_restores_per_bucket(svc):
    svc.grant(U, "free_monthly", 20, source_type="system")
    svc.grant(U, "member", 100, source_type="order")
    svc.consume(U, 50, job_id="job-1")  # 20 free + 30 member
    assert svc.available_minutes(U) == 70
    svc.refund_job(U, "job-1")
    b = svc.bucket_balances(U)
    assert b["free_monthly"] == 20 and b["member"] == 100
    with pytest.raises(DuplicateOperation):
        svc.refund_job(U, "job-1")  # 冲正幂等


def test_expired_bucket_not_usable(svc):
    svc.grant(U, "gift", 100, source_type="redeem",
              expires_at=NOW - timedelta(days=1))
    svc.grant(U, "member", 40, source_type="order")
    assert svc.available_minutes(U) == 40
    with pytest.raises(InsufficientMinutes):
        svc.consume(U, 60, job_id="job-1")


def test_expire_entitlement_writes_ledger(svc):
    ent = svc.grant(U, "gift", 100, source_type="redeem")
    svc.consume(U, 30, job_id="job-1")
    entry = svc.expire_entitlement(U, ent.id)
    assert entry.delta_minutes == -70 and entry.reason == "expire"
    # 全部流水净和为该用户真实余额 0
    assert sum(e.delta_minutes for e in svc.store.entries_for(U)) == 0
