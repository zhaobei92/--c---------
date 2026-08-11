"""Multipart 终结阶段一致性测试(审查 Phase 2.1 第二优先级)。

核心缺陷复现:S3 操作不随 PostgreSQL 事务回滚 —— Hash 不符路径中
删对象/新开 multipart 已发生,但异常导致事务回滚,新 s3_upload_id 与
分片重置全部丢失,会话指向已失效的旧 Upload ID,永远无法恢复。

修复后的状态机:active → completing →(S3 finalize + 校验,事务外)
→ completed;失败分支在独立事务中持久化修复动作;Reconciler 处理
"事务间隙被杀"的所有中间态。
"""

import hashlib
from concurrent.futures import ThreadPoolExecutor

import pytest
import requests
from sqlalchemy import func, select

pytestmark = pytest.mark.integration

PART = 5 * 1024 * 1024


@pytest.fixture
def sf(db):
    from app.db.engine import make_session_factory
    return make_session_factory(db)


@pytest.fixture
def golden(sf):
    from app.db.golden import GoldenChainDb
    return GoldenChainDb(sf)


@pytest.fixture
def store(s3_client):
    from app.services.s3_store import S3ObjectStore
    from tests.integration.conftest import S3_ENDPOINT, S3_KEY, S3_SECRET
    st = S3ObjectStore(endpoint=S3_ENDPOINT, access_key=S3_KEY,
                       secret_key=S3_SECRET, bucket="ysnote-audio")
    st.ensure_bucket()
    return st


@pytest.fixture
def uploads(sf, store):
    from app.db.uploads import PgUploadService
    return PgUploadService(sf, store)


def _setup(golden, uploads, data: bytes, declared: bytes | None = None):
    """登记 declared(默认=data)的元数据;实际上传 data。"""
    declared = declared if declared is not None else data
    sha = hashlib.sha256(declared).hexdigest()
    user_id, _ = golden.ensure_user("a@test.com")
    rec = golden.create_recording(user_id, {
        "title": "audio", "duration_ms": 60000,
        "sha256": sha, "size_bytes": len(declared),
    })
    init = uploads.init_upload(user_id=user_id, recording_id=rec["id"],
                               size_bytes=len(declared), sha256=sha,
                               part_size=PART)
    return user_id, rec, init


def _put_all(uploads, user_id, init, data):
    for p in init["parts"]:
        n = p["part_no"]
        chunk = data[(n - 1) * init["part_size"]: n * init["part_size"]]
        resp = requests.put(p["put_url"], data=chunk, timeout=30)
        etag = resp.headers["ETag"].strip('"')
        uploads.register_part(user_id=user_id, upload_id=init["upload_id"],
                              part_no=n, etag=etag, size_bytes=len(chunk))


def _session_row(sf, upload_id):
    from app.models.tables import UploadSessionRow
    with sf() as s:
        return s.get(UploadSessionRow, upload_id)


def test_hash_mismatch_reopens_upload_and_recovery_succeeds(
        db, sf, golden, uploads):
    """核心回归:合并成功但 Hash 不符 → 重开的 multipart 必须持久化,
    分片重置为 pending,重新上传正确内容后 complete 成功。

    修复前:异常回滚吞掉新 s3_upload_id,会话指向失效 Upload ID,永远卡死。
    """
    from app.core.errors import ApiError

    good = bytes((i * 13) % 251 for i in range(PART + 4096))
    bad = bytes((i * 7) % 251 for i in range(PART + 4096))  # 同长不同内容
    user_id, rec, init = _setup(golden, uploads, data=bad, declared=good)
    old_s3_id = _session_row(sf, init["upload_id"]).s3_upload_id

    _put_all(uploads, user_id, init, bad)  # 上传了错误内容
    with pytest.raises(ApiError) as e:
        uploads.complete(user_id=user_id, upload_id=init["upload_id"])
    assert e.value.code == "UPL_2103"

    # 修复动作必须已持久化:新 s3_upload_id 落库、分片回 pending、会话可恢复
    row = _session_row(sf, init["upload_id"])
    assert row.status == "active"
    assert row.s3_upload_id != old_s3_id, "重开的 multipart ID 必须写入数据库"
    progress = uploads.progress(user_id=user_id, upload_id=init["upload_id"])
    assert progress["pending_parts"] == [1, 2], "分片必须重置为 pending"

    # 用正确内容重传 → 成功完成
    _put_all(uploads, user_id, progress | {"upload_id": init["upload_id"],
                                           "part_size": init["part_size"]}, good)
    done = uploads.complete(user_id=user_id, upload_id=init["upload_id"])
    assert done["completed"] is True


def test_recovery_after_s3_complete_but_db_crash(db, sf, golden, uploads, store):
    """S3 合并成功、数据库落账前进程被杀:会话停在 completing,
    Reconciler 继续校验并完成落库(同一路径覆盖"API 中途被杀")。"""
    data = bytes((i * 11) % 251 for i in range(PART + 512))
    user_id, rec, init = _setup(golden, uploads, data)
    _put_all(uploads, user_id, init, data)

    # 手工执行前两步(= 在 finalize 落库前崩溃)
    uploads.mark_completing(user_id=user_id, upload_id=init["upload_id"])
    uploads.s3_finalize(upload_id=init["upload_id"])
    assert _session_row(sf, init["upload_id"]).status == "completing"

    recovered = uploads.reconcile()
    assert recovered >= 1
    row = _session_row(sf, init["upload_id"])
    assert row.status == "completed"
    from app.models.tables import MediaAsset
    with sf() as s:
        assert s.execute(select(func.count()).select_from(MediaAsset)).scalar() == 1
    assert golden.get_recording(user_id, rec["id"])["cloud_status"] == "uploaded"


def test_concurrent_complete_yields_single_asset(db, sf, golden, uploads):
    """同一 complete 并发两次:恰好一个 media_asset,双方都拿到完成结果。"""
    data = bytes((i * 3) % 251 for i in range(PART + 256))
    user_id, _, init = _setup(golden, uploads, data)
    _put_all(uploads, user_id, init, data)

    def run(_):
        try:
            return uploads.complete(user_id=user_id, upload_id=init["upload_id"])
        except Exception as e:
            return {"error": str(e)}

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(run, range(2)))
    assert any(r.get("completed") for r in results), results

    from app.models.tables import MediaAsset
    with sf() as s:
        assert s.execute(select(func.count()).select_from(MediaAsset)).scalar() == 1


def test_reconciler_reopens_dead_upload(db, sf, golden, uploads, store):
    """孤儿场景:completing 会话但对象不存在、multipart 已失效 →
    Reconciler 开新 multipart、保存新 ID、分片回 pending,可重新上传。"""
    data = bytes((i * 5) % 251 for i in range(PART + 128))
    user_id, _, init = _setup(golden, uploads, data)
    _put_all(uploads, user_id, init, data)
    uploads.mark_completing(user_id=user_id, upload_id=init["upload_id"])
    # 模拟灾难:multipart 被外部 abort,对象不存在
    row = _session_row(sf, init["upload_id"])
    store.abort_multipart_upload(row.storage_key, row.s3_upload_id)

    assert uploads.reconcile() >= 1
    fresh = _session_row(sf, init["upload_id"])
    assert fresh.status == "active"
    assert fresh.s3_upload_id != row.s3_upload_id
    progress = uploads.progress(user_id=user_id, upload_id=init["upload_id"])
    assert progress["pending_parts"] == [1, 2]

    _put_all(uploads, user_id, progress | {"upload_id": init["upload_id"],
                                           "part_size": init["part_size"]}, data)
    assert uploads.complete(user_id=user_id,
                            upload_id=init["upload_id"])["completed"]
