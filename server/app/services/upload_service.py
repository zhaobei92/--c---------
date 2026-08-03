"""分片上传服务核心逻辑(纯 Python,可独立单测)。

流程(docs/04-api-spec.md §4):
  init → 逐分片 PUT(预签名 URL,由 ObjectStore 提供)→ 逐分片 complete 登记
  → complete 合并 + 整体 SHA-256 校验。

关键约束:
  * init 时按 sha256 全局去重(media_assets.sha256 唯一)——同一文件不重复上传、不重复收费。
  * 分片登记幂等:同 (upload_id, part_no) 重复登记返回原结果。
  * 缺片拒绝合并(UPL_2102);整体 hash 不一致拒绝并保留分片(UPL_2103)。
"""

from __future__ import annotations

import math
import uuid
from dataclasses import dataclass, field
from typing import Protocol

DEFAULT_PART_SIZE = 6 * 1024 * 1024  # 4—8MB 检查点区间取 6MB
MAX_FILE_BYTES = 2 * 1024 * 1024 * 1024  # 单文件上限 2GB


class UploadError(Exception):
    error_code = "UPL_2001"


class FileTooLarge(UploadError):
    error_code = "UPL_2002"


class MissingParts(UploadError):
    error_code = "UPL_2102"

    def __init__(self, missing: list[int]):
        self.missing = missing
        super().__init__(f"missing parts: {missing}")


class HashMismatch(UploadError):
    error_code = "UPL_2103"


class ObjectStore(Protocol):
    """S3 兼容对象存储抽象;生产实现走 boto3,单测用 FakeObjectStore。"""

    def presign_put(self, key: str, part_no: int) -> str: ...
    def merge_parts(self, key: str, part_nos: list[int]) -> str: ...
    def compute_sha256(self, key: str) -> str: ...


@dataclass
class UploadPart:
    part_no: int
    put_url: str
    status: str = "pending"  # pending / uploaded
    etag: str | None = None
    size_bytes: int | None = None


@dataclass
class UploadSession:
    upload_id: str
    recording_id: str
    user_id: str
    size_bytes: int
    sha256: str
    part_size: int
    parts: dict[int, UploadPart]
    status: str = "active"  # active / completed / aborted
    media_asset_id: str | None = None
    storage_key: str = ""


@dataclass
class InitResult:
    deduplicated: bool
    media_asset_id: str | None = None
    session: UploadSession | None = None


class UploadService:
    def __init__(self, store: ObjectStore):
        self.store = store
        self.sessions: dict[str, UploadSession] = {}
        # sha256 -> media_asset_id(生产实现为 media_assets 表)
        self.assets_by_sha: dict[str, str] = {}

    def init_upload(
        self,
        *,
        user_id: str,
        recording_id: str,
        size_bytes: int,
        sha256: str,
        part_size: int = DEFAULT_PART_SIZE,
    ) -> InitResult:
        if size_bytes <= 0 or size_bytes > MAX_FILE_BYTES:
            raise FileTooLarge(f"size {size_bytes} out of range")
        if sha256 in self.assets_by_sha:
            return InitResult(deduplicated=True, media_asset_id=self.assets_by_sha[sha256])
        upload_id = str(uuid.uuid4())
        storage_key = f"audio/{user_id}/{recording_id}/{upload_id}"
        n_parts = max(1, math.ceil(size_bytes / part_size))
        parts = {
            n: UploadPart(part_no=n, put_url=self.store.presign_put(storage_key, n))
            for n in range(1, n_parts + 1)
        }
        session = UploadSession(
            upload_id=upload_id, recording_id=recording_id, user_id=user_id,
            size_bytes=size_bytes, sha256=sha256, part_size=part_size,
            parts=parts, storage_key=storage_key,
        )
        self.sessions[upload_id] = session
        return InitResult(deduplicated=False, session=session)

    def register_part(self, upload_id: str, part_no: int, *, etag: str, size_bytes: int) -> UploadPart:
        session = self._session(upload_id)
        part = session.parts.get(part_no)
        if part is None:
            raise UploadError(f"unknown part {part_no}")
        if part.status == "uploaded":
            return part  # 幂等:重复登记返回原结果
        part.status, part.etag, part.size_bytes = "uploaded", etag, size_bytes
        return part

    def pending_parts(self, upload_id: str) -> list[int]:
        """断点续传:App 重启后拉取未完成分片列表。"""
        session = self._session(upload_id)
        return sorted(n for n, p in session.parts.items() if p.status != "uploaded")

    def complete(self, upload_id: str) -> UploadSession:
        session = self._session(upload_id)
        if session.status == "completed":
            return session  # 幂等
        missing = self.pending_parts(upload_id)
        if missing:
            raise MissingParts(missing)
        self.store.merge_parts(session.storage_key, sorted(session.parts))
        actual = self.store.compute_sha256(session.storage_key)
        if actual != session.sha256:
            # 分片保留,客户端可重传后再 complete
            raise HashMismatch(f"expected {session.sha256}, got {actual}")
        session.status = "completed"
        session.media_asset_id = str(uuid.uuid4())
        self.assets_by_sha[session.sha256] = session.media_asset_id
        return session

    def abort(self, upload_id: str) -> None:
        self._session(upload_id).status = "aborted"

    def _session(self, upload_id: str) -> UploadSession:
        session = self.sessions.get(upload_id)
        if session is None or session.status == "aborted":
            raise UploadError(f"upload {upload_id} not found or aborted")
        return session
