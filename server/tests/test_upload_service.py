import hashlib

import pytest

from app.api.deps import FakeObjectStore
from app.services.upload_service import (
    FileTooLarge, HashMismatch, MissingParts, UploadService,
)


@pytest.fixture
def svc():
    return UploadService(FakeObjectStore())


def _upload_all(svc, data: bytes, part_size: int = 4):
    sha = hashlib.sha256(data).hexdigest()
    result = svc.init_upload(user_id="u1", recording_id="r1",
                             size_bytes=len(data), sha256=sha, part_size=part_size)
    s = result.session
    for n in sorted(s.parts):
        chunk = data[(n - 1) * part_size: n * part_size]
        etag = svc.store.put_part(s.storage_key, n, chunk)
        svc.register_part(s.upload_id, n, etag=etag, size_bytes=len(chunk))
    return s


def test_full_chunked_upload_and_merge(svc):
    data = b"0123456789abcdef!"  # 17 bytes → 5 parts of 4
    s = _upload_all(svc, data)
    assert len(s.parts) == 5
    done = svc.complete(s.upload_id)
    assert done.status == "completed"
    assert done.media_asset_id
    assert svc.store.objects[s.storage_key] == data


def test_init_dedup_by_sha256(svc):
    data = b"same-file"
    s = _upload_all(svc, data)
    svc.complete(s.upload_id)
    again = svc.init_upload(user_id="u2", recording_id="r2",
                            size_bytes=len(data),
                            sha256=hashlib.sha256(data).hexdigest())
    # 同一文件不重复上传、不重复收费
    assert again.deduplicated is True
    assert again.media_asset_id == s.media_asset_id


def test_part_registration_idempotent(svc):
    data = b"12345678"
    sha = hashlib.sha256(data).hexdigest()
    s = svc.init_upload(user_id="u1", recording_id="r1",
                        size_bytes=8, sha256=sha, part_size=4).session
    svc.store.put_part(s.storage_key, 1, data[:4])
    p1 = svc.register_part(s.upload_id, 1, etag="e1", size_bytes=4)
    p2 = svc.register_part(s.upload_id, 1, etag="e-different", size_bytes=4)
    assert p1 is p2 and p2.etag == "e1"


def test_missing_parts_rejected(svc):
    data = b"12345678"
    sha = hashlib.sha256(data).hexdigest()
    s = svc.init_upload(user_id="u1", recording_id="r1",
                        size_bytes=8, sha256=sha, part_size=4).session
    svc.store.put_part(s.storage_key, 1, data[:4])
    svc.register_part(s.upload_id, 1, etag="e1", size_bytes=4)
    with pytest.raises(MissingParts) as e:
        svc.complete(s.upload_id)
    assert e.value.missing == [2]
    assert svc.pending_parts(s.upload_id) == [2]  # 断点续传视图


def test_hash_mismatch_keeps_parts(svc):
    data = b"12345678"
    s = svc.init_upload(user_id="u1", recording_id="r1", size_bytes=8,
                        sha256="0" * 64, part_size=4).session
    for n in (1, 2):
        svc.store.put_part(s.storage_key, n, data[(n - 1) * 4: n * 4])
        svc.register_part(s.upload_id, n, etag=f"e{n}", size_bytes=4)
    with pytest.raises(HashMismatch):
        svc.complete(s.upload_id)
    assert s.status == "active"          # 分片保留可重传
    assert svc.pending_parts(s.upload_id) == []


def test_file_too_large(svc):
    with pytest.raises(FileTooLarge):
        svc.init_upload(user_id="u1", recording_id="r1",
                        size_bytes=3 * 1024 ** 3, sha256="x" * 64)


def test_complete_idempotent(svc):
    data = b"abcd"
    s = _upload_all(svc, data)
    first = svc.complete(s.upload_id)
    second = svc.complete(s.upload_id)
    assert first.media_asset_id == second.media_asset_id
