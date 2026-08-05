"""真实 S3 Multipart 上传集成测试(审查要求的 10 项全覆盖)。

端点:YS_S3_ENDPOINT(CI=MinIO 容器;本地=moto server,同一 boto3 代码路径)。
"""

import hashlib

import pytest
import requests

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


PART = 5 * 1024 * 1024  # S3 非最后分片最小 5MB


def _setup(golden, uploads, *, email="a@test.com", n_bytes=PART * 2 + 1024):
    data = bytes((i * 31) % 251 for i in range(n_bytes))  # 3 个分片
    sha = hashlib.sha256(data).hexdigest()
    user_id, _ = golden.ensure_user(email)
    rec = golden.create_recording(user_id, {
        "title": "audio", "duration_ms": 60000,
        "sha256": sha, "size_bytes": len(data),
    })
    init = uploads.init_upload(user_id=user_id, recording_id=rec["id"],
                               size_bytes=len(data), sha256=sha, part_size=PART)
    return user_id, rec, data, sha, init


def _put_parts(uploads, user_id, init, data, part_nos=None):
    for p in init["parts"]:
        n = p["part_no"]
        if part_nos is not None and n not in part_nos:
            continue
        chunk = data[(n - 1) * init["part_size"]: n * init["part_size"]]
        resp = requests.put(p["put_url"], data=chunk, timeout=30)
        assert resp.status_code == 200, resp.text
        etag = resp.headers["ETag"].strip('"')
        uploads.register_part(user_id=user_id, upload_id=init["upload_id"],
                              part_no=n, etag=etag, size_bytes=len(chunk))


def test_full_three_part_upload_and_hash(db, golden, uploads, store):
    """①③⑥:真实 PUT 三分片、预签名 URL、完整 Hash 校验、资产落库。"""
    user_id, rec, data, sha, init = _setup(golden, uploads)
    assert init["total_parts"] == 3
    _put_parts(uploads, user_id, init, data)
    done = uploads.complete(user_id=user_id, upload_id=init["upload_id"])
    assert done["completed"] is True
    assert golden.get_recording(user_id, rec["id"])["cloud_status"] == "uploaded"
    # 对象内容与原文件逐字节一致
    assert store.stream_sha256(_storage_key(db, init["upload_id"])) == sha


def _storage_key(engine, upload_id):
    from app.db.engine import make_session_factory
    from app.models.tables import UploadSessionRow
    with make_session_factory(engine)() as s:
        return s.get(UploadSessionRow, upload_id).storage_key


def test_resume_after_api_restart(db, golden, uploads, sf, store):
    """②③:传 1 片后"重启 API"(新服务实例),会话从 PG 恢复并重签 URL 续传。"""
    user_id, rec, data, sha, init = _setup(golden, uploads)
    _put_parts(uploads, user_id, init, data, part_nos={1})

    from app.db.uploads import PgUploadService
    reborn = PgUploadService(sf, store)  # 新实例 = 重启后的 API
    progress = reborn.progress(user_id=user_id, upload_id=init["upload_id"])
    assert progress["pending_parts"] == [2, 3]
    assert all(p["put_url"].startswith("http") for p in progress["parts"])  # 重签
    _put_parts(reborn, user_id, progress | {"upload_id": init["upload_id"],
                                            "part_size": init["part_size"]}, data)
    assert reborn.complete(user_id=user_id, upload_id=init["upload_id"])["completed"]


def test_missing_parts_rejected(db, golden, uploads):
    """④:少分片拒绝合并。"""
    from app.core.errors import ApiError
    user_id, _, data, _, init = _setup(golden, uploads)
    _put_parts(uploads, user_id, init, data, part_nos={1, 3})
    with pytest.raises(ApiError) as e:
        uploads.complete(user_id=user_id, upload_id=init["upload_id"])
    assert e.value.code == "UPL_2102"
    assert e.value.detail["missing"] == [2]


def test_wrong_etag_rejected(db, golden, uploads):
    """⑤:ETag 错误被 S3 拒绝合并,分片记录保留。"""
    from app.core.errors import ApiError
    user_id, _, data, _, init = _setup(golden, uploads)
    _put_parts(uploads, user_id, init, data, part_nos={2, 3})
    chunk = data[:init["part_size"]]
    requests.put(init["parts"][0]["put_url"], data=chunk, timeout=30)
    uploads.register_part(user_id=user_id, upload_id=init["upload_id"],
                          part_no=1, etag="deadbeef" * 4, size_bytes=len(chunk))
    with pytest.raises(ApiError) as e:
        uploads.complete(user_id=user_id, upload_id=init["upload_id"])
    assert e.value.code == "UPL_2103"


def test_cross_user_access_rejected(db, golden, uploads):
    """⑦:用户 B 不能查询或完成用户 A 的上传。"""
    from app.core.errors import ApiError
    user_a, _, data, _, init = _setup(golden, uploads)
    user_b, _ = golden.ensure_user("b@test.com")
    for op in (
        lambda: uploads.progress(user_id=user_b, upload_id=init["upload_id"]),
        lambda: uploads.complete(user_id=user_b, upload_id=init["upload_id"]),
        lambda: uploads.register_part(user_id=user_b, upload_id=init["upload_id"],
                                      part_no=1, etag="x", size_bytes=1),
        lambda: uploads.abort(user_id=user_b, upload_id=init["upload_id"]),
    ):
        with pytest.raises(ApiError) as e:
            op()
        assert e.value.code == "DOC_6003"


def test_stale_session_auto_aborted(db, golden, uploads, sf):
    """⑧:上传超时自动 Abort。"""
    from datetime import datetime, timedelta, timezone
    from app.core.errors import ApiError
    from app.models.tables import UploadSessionRow

    user_id, _, data, _, init = _setup(golden, uploads)
    with sf() as s, s.begin():
        row = s.get(UploadSessionRow, init["upload_id"])
        row.expires_at = datetime.now(timezone.utc) - timedelta(hours=1)
    assert uploads.expire_stale() == 1
    with pytest.raises(ApiError):
        uploads.register_part(user_id=user_id, upload_id=init["upload_id"],
                              part_no=1, etag="x", size_bytes=1)


def test_same_user_dedup_and_cross_user_no_dedup(db, golden, uploads):
    """⑨⑩:同用户同 Hash 去重;不同用户同 Hash 不去重。"""
    user_a, rec, data, sha, init = _setup(golden, uploads, email="a@test.com")
    _put_parts(uploads, user_a, init, data)
    uploads.complete(user_id=user_a, upload_id=init["upload_id"])

    # 同用户再次登记同内容录音 → recordings 去重返回原纪录;init 直接命中资产
    rec2 = golden.create_recording(user_a, {
        "title": "copy", "duration_ms": 60000, "sha256": sha,
        "size_bytes": len(data)})
    assert rec2["deduplicated"] is True
    again = uploads.init_upload(user_id=user_a, recording_id=rec2["id"],
                                size_bytes=len(data), sha256=sha)
    assert again["deduplicated"] is True

    # 不同用户同内容 → 不去重,需要真实上传
    user_b, _ = golden.ensure_user("b@test.com")
    rec_b = golden.create_recording(user_b, {
        "title": "same-bytes", "duration_ms": 60000, "sha256": sha,
        "size_bytes": len(data)})
    init_b = uploads.init_upload(user_id=user_b, recording_id=rec_b["id"],
                                 size_bytes=len(data), sha256=sha, part_size=PART)
    assert init_b["deduplicated"] is False
