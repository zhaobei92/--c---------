from datetime import datetime, timedelta, timezone

import pytest

from app.services.entitlement_service import EntitlementService, InMemoryLedgerStore
from app.services.order_verification import (
    DEFAULT_CATALOG, OrderService, UnknownProduct, VerificationFailed, VerifiedTransaction,
)

NOW = datetime(2026, 8, 3, tzinfo=timezone.utc)


class FakeProvider:
    """凭证格式 'txn_id:product_id',便于测试各分支。"""

    platform = "apple"

    def verify(self, credential: str) -> VerifiedTransaction:
        if credential == "bad":
            raise VerificationFailed("apple", "invalid signature")
        txn_id, product_id = credential.split(":")
        return VerifiedTransaction(
            transaction_id=txn_id, product_id=product_id,
            purchased_at=NOW, expires_at=NOW + timedelta(days=30),
        )


@pytest.fixture
def svc():
    ents = EntitlementService(InMemoryLedgerStore(), now=lambda: NOW)
    return OrderService(ents, DEFAULT_CATALOG, {"apple": FakeProvider()}, now=lambda: NOW)


def test_verify_grants_entitlement(svc):
    order, already = svc.verify_and_fulfill(
        user_id="u1", platform="apple", credential="t1:ys_member_monthly")
    assert already is False and order.status == "verified"
    assert svc.entitlements.bucket_balances("u1")["member"] == 1200


def test_verify_idempotent_same_transaction(svc):
    # 幂等锚点 = transaction_id:重复验证不重复发放
    svc.verify_and_fulfill(user_id="u1", platform="apple", credential="t1:ys_minutes_300")
    order2, already = svc.verify_and_fulfill(
        user_id="u1", platform="apple", credential="t1:ys_minutes_300")
    assert already is True
    assert svc.entitlements.bucket_balances("u1")["purchased"] == 300


def test_transaction_owned_by_other_account_rejected(svc):
    svc.verify_and_fulfill(user_id="u1", platform="apple", credential="t1:ys_minutes_300")
    with pytest.raises(Exception) as e:
        svc.verify_and_fulfill(user_id="u2", platform="apple", credential="t1:ys_minutes_300")
    assert "ORD_5005" in str(e.value)


def test_bad_credential(svc):
    with pytest.raises(VerificationFailed):
        svc.verify_and_fulfill(user_id="u1", platform="apple", credential="bad")


def test_unknown_product_alerts(svc):
    with pytest.raises(UnknownProduct):
        svc.verify_and_fulfill(user_id="u1", platform="apple", credential="t9:nonexistent")


def test_refund_expires_granted_minutes(svc):
    svc.verify_and_fulfill(user_id="u1", platform="apple", credential="t1:ys_minutes_1000")
    assert svc.entitlements.bucket_balances("u1")["purchased"] == 1000
    order = svc.refund("t1")
    assert order.status == "refunded"
    assert svc.entitlements.bucket_balances("u1")["purchased"] == 0
    # 冲正落 expire 流水,余额净和为 0
    assert sum(e.delta_minutes for e in svc.entitlements.store.entries_for("u1")) == 0
    assert svc.refund("t1").status == "refunded"  # 幂等
