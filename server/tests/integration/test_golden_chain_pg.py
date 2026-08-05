"""黄金主链 PostgreSQL 集成测试:用户 → 录音 → 权益 → 任务 → Outbox。

含审查要求的并发验收:20 个并发同录音建任务 → 只有一个任务、只扣一次分钟、
只有一条 Outbox 事件、其余请求幂等返回、余额不出现负数。
"""

import hashlib
import uuid
from concurrent.futures import ThreadPoolExecutor

import pytest
from sqlalchemy import func, select

pytestmark = pytest.mark.integration

FREE = 120  # settings.free_monthly_minutes 默认值


@pytest.fixture
def golden(db):
    from app.db.engine import make_session_factory
    from app.db.golden import GoldenChainDb
    return GoldenChainDb(make_session_factory(db))


def _recording(golden, user_id, minutes=5, salt="a"):
    data = f"audio-{salt}".encode()
    return golden.create_recording(user_id, {
        "title": "rec", "duration_ms": minutes * 60000,
        "sha256": hashlib.sha256(data).hexdigest(), "size_bytes": len(data),
    })


def test_user_creation_grants_free_minutes_and_persists(db, golden):
    user_id, is_new = golden.ensure_user("a@test.com")
    assert is_new
    assert golden.balances(user_id)["free_monthly"] == FREE
    # 幂等:同邮箱再次 ensure 返回同一用户,不重复发放
    same_id, is_new2 = golden.ensure_user("a@test.com")
    assert same_id == user_id and not is_new2
    assert golden.balances(user_id)["free_monthly"] == FREE

    # 模拟 API 重启:新引擎/新会话工厂,数据仍在(退出标准:重启后状态不丢)
    from app.db.engine import make_engine, make_session_factory
    from app.db.golden import GoldenChainDb
    from tests.integration.conftest import PG_URL
    fresh = GoldenChainDb(make_session_factory(make_engine(PG_URL)))
    assert fresh.get_user(user_id)["email"] == "a@test.com"
    assert fresh.balances(user_id)["free_monthly"] == FREE


def test_recording_dedup_per_user(db, golden):
    ua, _ = golden.ensure_user("a@test.com")
    ub, _ = golden.ensure_user("b@test.com")
    r1 = _recording(golden, ua, salt="same")
    r2 = _recording(golden, ua, salt="same")
    assert r2["deduplicated"] is True and r2["id"] == r1["id"]
    r3 = _recording(golden, ub, salt="same")  # 跨用户不去重
    assert r3["deduplicated"] is False


def test_create_job_transactional(db, golden):
    user_id, _ = golden.ensure_user("a@test.com")
    rec = _recording(golden, user_id, minutes=5)
    job = golden.create_job(user_id, rec["id"])
    assert job["deduplicated"] is False
    assert job["status"] == "waiting" and job["minutes_charged"] == 5
    assert golden.balances(user_id)["free_monthly"] == FREE - 5

    from app.models.tables import OutboxEvent
    from app.db.engine import make_session_factory
    with make_session_factory(db)() as s:
        events = s.execute(select(OutboxEvent)).scalars().all()
        assert len(events) == 1
        assert events[0].payload["job_id"] == job["id"]
        assert events[0].published_at is None


def test_20_concurrent_creates_charge_once(db, golden):
    user_id, _ = golden.ensure_user("a@test.com")
    rec = _recording(golden, user_id, minutes=7)

    with ThreadPoolExecutor(max_workers=20) as pool:
        results = list(pool.map(
            lambda _: golden.create_job(user_id, rec["id"]), range(20)))

    fresh = [r for r in results if not r["deduplicated"]]
    dedup = [r for r in results if r["deduplicated"]]
    assert len(fresh) == 1, f"必须只创建一个任务,实际 {len(fresh)}"
    assert len(dedup) == 19
    assert {r["id"] for r in results} == {fresh[0]["id"]}  # 全部指向同一任务

    from app.models.tables import OutboxEvent, TranscriptionJob, UsageLedger
    from app.db.engine import make_session_factory
    with make_session_factory(db)() as s:
        assert s.execute(select(func.count()).select_from(TranscriptionJob)).scalar() == 1
        assert s.execute(select(func.count()).select_from(OutboxEvent)).scalar() == 1
        consumes = s.execute(
            select(UsageLedger).where(UsageLedger.reason == "consume")
        ).scalars().all()
        assert sum(-e.delta_minutes for e in consumes) == 7  # 只扣一次
    balance = golden.balances(user_id)
    assert balance["free_monthly"] == FREE - 7
    assert all(v >= 0 for v in balance.values())  # 无负余额


def test_insufficient_minutes_rejected_atomically(db, golden):
    from app.core.errors import ApiError
    user_id, _ = golden.ensure_user("a@test.com")
    rec = _recording(golden, user_id, minutes=FREE + 1)
    with pytest.raises(ApiError) as e:
        golden.create_job(user_id, rec["id"])
    assert e.value.code == "ENT_3001"
    # 原子性:失败后无任务、无 outbox、无扣费流水
    from app.models.tables import OutboxEvent, TranscriptionJob, UsageLedger
    from app.db.engine import make_session_factory
    with make_session_factory(db)() as s:
        assert s.execute(select(func.count()).select_from(TranscriptionJob)).scalar() == 0
        assert s.execute(select(func.count()).select_from(OutboxEvent)).scalar() == 0
        assert s.execute(select(func.count()).select_from(UsageLedger)
                         .where(UsageLedger.reason == "consume")).scalar() == 0


def test_cross_user_job_creation_rejected(db, golden):
    from app.core.errors import ApiError
    ua, _ = golden.ensure_user("a@test.com")
    ub, _ = golden.ensure_user("b@test.com")
    rec = _recording(golden, ua)
    with pytest.raises(ApiError) as e:
        golden.create_job(ub, rec["id"])
    assert e.value.code == "DOC_6003"


def test_retry_after_refund_recharges(db, golden):
    """P0-6 语义在 PG 上成立:退款后的人工重试按新 generation 重新扣费。"""
    from app.db.engine import make_session_factory
    from app.db.ledger import SqlLedgerStore
    from app.models.tables import TranscriptionJob
    from app.services.entitlement_service import EntitlementService

    user_id, _ = golden.ensure_user("a@test.com")
    rec = _recording(golden, user_id, minutes=5)
    job = golden.create_job(user_id, rec["id"])
    assert golden.balances(user_id)["free_monthly"] == FREE - 5

    # 模拟 worker:终态失败 + 冲正当代扣费(④ 的 db_worker 将执行同一逻辑)
    sf = make_session_factory(db)
    with sf() as s, s.begin():
        row = s.get(TranscriptionJob, job["id"])
        row.status = "failed"
        row.error_code = "JOB_4201"
        EntitlementService(SqlLedgerStore(s)).refund_job(user_id, job["id"], generation=0)
    assert golden.balances(user_id)["free_monthly"] == FREE

    retried = golden.retry_job(user_id, job["id"])
    assert retried["status"] == "waiting"
    assert golden.balances(user_id)["free_monthly"] == FREE - 5  # 新一代已扣

    entries = golden.usage_entries(user_id)
    consumes = [e for e in entries if e["reason"] == "consume"]
    assert sorted(e["generation"] for e in consumes) == [0, 1]
    assert all(e["job_id"] == job["id"] for e in consumes)  # UUID 外键,无后缀
