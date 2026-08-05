"""PG 持久化的分片上传服务(真实 S3 Multipart)。

会话与分片全部落库(upload_sessions / upload_parts):API 重启后凭 DB 恢复,
预签名 URL 按需重签。流程见 docs/04-api-spec.md §4。
"""

from __future__ import annotations

import math
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from ..core.errors import ApiError
from ..models.tables import (
    MediaAsset, Recording, UploadPart, UploadSessionRow,
)
from ..services.s3_store import S3ObjectStore

DEFAULT_PART_SIZE = 6 * 1024 * 1024
MAX_FILE_BYTES = 2 * 1024 * 1024 * 1024
SESSION_TTL = timedelta(hours=24)


def _now() -> datetime:
    return datetime.now(timezone.utc)


class PgUploadService:
    def __init__(self, session_factory: sessionmaker[Session], store: S3ObjectStore):
        self.sf = session_factory
        self.store = store

    # ---------------- init

    def init_upload(self, *, user_id: str, recording_id: str,
                    size_bytes: int, sha256: str,
                    part_size: int = DEFAULT_PART_SIZE) -> dict:
        if size_bytes <= 0 or size_bytes > MAX_FILE_BYTES:
            raise ApiError("UPL_2002", detail={"size_bytes": size_bytes})
        with self.sf() as s, s.begin():
            rec = s.get(Recording, recording_id)
            if rec is None or str(rec.user_id) != user_id or rec.deleted_at is not None:
                raise ApiError("DOC_6003")
            if rec.sha256 != sha256 or rec.size_bytes != size_bytes:
                raise ApiError("SYS_9004",
                               detail={"reason": "metadata mismatch with recording"})
            # 单用户去重:同 hash 已有资产 → 不再上传、不再收费
            asset = s.execute(select(MediaAsset).where(
                MediaAsset.user_id == user_id, MediaAsset.sha256 == sha256,
            )).scalar_one_or_none()
            if asset is not None:
                rec.cloud_status = "uploaded"
                rec.media_asset_id = asset.id
                return {"deduplicated": True, "media_asset_id": str(asset.id)}

            # 已有活跃会话 → 幂等返回续传视图
            existing = s.execute(select(UploadSessionRow).where(
                UploadSessionRow.recording_id == recording_id,
                UploadSessionRow.status == "active",
            )).scalar_one_or_none()
            if existing is not None:
                return self._session_view(s, existing)

            key = f"audio/{user_id}/{recording_id}/{uuid.uuid4()}"
            s3_upload_id = self.store.create_multipart_upload(key)
            session = UploadSessionRow(
                id=str(uuid.uuid4()), user_id=user_id, recording_id=recording_id,
                storage_key=key, s3_upload_id=s3_upload_id,
                size_bytes=size_bytes, part_size=part_size, sha256=sha256,
                status="active", expires_at=_now() + SESSION_TTL,
            )
            s.add(session)
            n_parts = max(1, math.ceil(size_bytes / part_size))
            for n in range(1, n_parts + 1):
                s.add(UploadPart(id=str(uuid.uuid4()), upload_id=session.id,
                                 recording_id=recording_id, part_no=n,
                                 status="pending"))
            s.flush()
            return self._session_view(s, session)

    # ---------------- parts

    def register_part(self, *, user_id: str, upload_id: str, part_no: int,
                      etag: str, size_bytes: int) -> dict:
        with self.sf() as s, s.begin():
            session = self._owned_active(s, user_id, upload_id)
            part = s.execute(select(UploadPart).where(
                UploadPart.upload_id == session.id, UploadPart.part_no == part_no,
            ).with_for_update()).scalar_one_or_none()
            if part is None:
                raise ApiError("UPL_2101", detail={"part_no": part_no})
            if part.status == "uploaded":
                return {"part_no": part.part_no, "status": "uploaded"}  # 幂等
            part.status = "uploaded"
            part.etag = etag
            part.size_bytes = size_bytes
            return {"part_no": part.part_no, "status": "uploaded"}

    def progress(self, *, user_id: str, upload_id: str) -> dict:
        with self.sf() as s:
            session = self._owned(s, user_id, upload_id)
            return self._session_view(s, session)

    # ---------------- complete

    def complete(self, *, user_id: str, upload_id: str) -> dict:
        with self.sf() as s, s.begin():
            session = self._owned(s, user_id, upload_id)
            if session.status == "completed":
                rec = s.get(Recording, session.recording_id)
                return {"completed": True,
                        "media_asset_id": str(rec.media_asset_id)}  # 幂等
            if session.status != "active":
                raise ApiError("DOC_6003")
            parts = s.execute(select(UploadPart).where(
                UploadPart.upload_id == session.id).order_by(UploadPart.part_no)
            ).scalars().all()
            missing = [p.part_no for p in parts if p.status != "uploaded"]
            if missing:
                raise ApiError("UPL_2102", detail={"missing": missing})

            try:
                self.store.complete_multipart_upload(
                    session.storage_key, session.s3_upload_id,
                    [{"PartNumber": p.part_no, "ETag": p.etag} for p in parts])
            except Exception as e:
                # ETag 不符/分片无效:S3 拒绝合并,保留分片记录供重传
                raise ApiError("UPL_2103", detail={"reason": str(e)[:200]})

            actual_size = self.store.head_size(session.storage_key)
            actual_sha = self.store.stream_sha256(session.storage_key)
            if actual_size != session.size_bytes or actual_sha != session.sha256:
                # 合并结果与登记不符:删对象、开新 multipart、分片重置为 pending
                self.store.delete_object(session.storage_key)
                session.s3_upload_id = self.store.create_multipart_upload(
                    session.storage_key)
                for p in parts:
                    p.status = "pending"
                    p.etag = None
                raise ApiError("UPL_2103", detail={
                    "expected_sha256": session.sha256, "actual_sha256": actual_sha})

            asset = MediaAsset(
                id=str(uuid.uuid4()), user_id=user_id, sha256=session.sha256,
                storage_key=session.storage_key, size_bytes=session.size_bytes,
            )
            s.add(asset)
            session.status = "completed"
            rec = s.get(Recording, session.recording_id)
            rec.cloud_status = "uploaded"
            rec.media_asset_id = asset.id
            s.flush()
            return {"completed": True, "media_asset_id": str(asset.id)}

    # ---------------- abort / 超时回收

    def abort(self, *, user_id: str, upload_id: str) -> None:
        with self.sf() as s, s.begin():
            session = self._owned(s, user_id, upload_id)
            self.store.abort_multipart_upload(session.storage_key, session.s3_upload_id)
            session.status = "aborted"

    def expire_stale(self) -> int:
        """超时会话自动 Abort(cron 调用);返回回收数。"""
        with self.sf() as s, s.begin():
            rows = s.execute(select(UploadSessionRow).where(
                UploadSessionRow.status == "active",
                UploadSessionRow.expires_at < _now(),
            ).with_for_update(skip_locked=True)).scalars().all()
            for row in rows:
                self.store.abort_multipart_upload(row.storage_key, row.s3_upload_id)
                row.status = "expired"
            return len(rows)

    # ---------------- 内部

    def _owned(self, s: Session, user_id: str, upload_id: str) -> UploadSessionRow:
        session = s.get(UploadSessionRow, upload_id)
        if session is None or str(session.user_id) != user_id:
            raise ApiError("DOC_6003")  # 跨用户:不泄露会话存在性
        return session

    def _owned_active(self, s: Session, user_id: str, upload_id: str) -> UploadSessionRow:
        session = self._owned(s, user_id, upload_id)
        if session.status != "active":
            raise ApiError("DOC_6003")
        return session

    def _session_view(self, s: Session, session: UploadSessionRow) -> dict:
        parts = s.execute(select(UploadPart).where(
            UploadPart.upload_id == session.id).order_by(UploadPart.part_no)
        ).scalars().all()
        pending = [p.part_no for p in parts if p.status != "uploaded"]
        return {
            "deduplicated": False,
            "upload_id": str(session.id),
            "status": session.status,
            "part_size": session.part_size,
            "total_parts": len(parts),
            "pending_parts": pending,
            # 预签名 URL 每次重签(过期自愈,断点续传直接可用)
            "parts": [
                {"part_no": n,
                 "put_url": self.store.presign_upload_part(
                     session.storage_key, session.s3_upload_id, n)}
                for n in pending
            ],
        }
