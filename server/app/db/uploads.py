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

    # ---------------- complete(状态机:active → completing → completed)
    #
    # S3 操作不随 PostgreSQL 事务回滚,因此终结阶段拆为三步,每步独立持久化:
    #   事务1  mark_completing:锁会话,校验分片齐全,active → completing
    #   事务外 s3_finalize:CompleteMultipartUpload(幂等重放:对象已存在即视为成功)
    #   事务2  _verify_and_finalize:Head+流式 SHA-256 校验 → 写 media_asset
    #          → 更新 recording → completed
    # 任一间隙被杀:会话停在 completing,由 reconcile() 恢复。
    # 失败分支(Hash 不符/multipart 失效)的修复动作在独立事务中先持久化再抛错。

    def complete(self, *, user_id: str, upload_id: str) -> dict:
        status = self.mark_completing(user_id=user_id, upload_id=upload_id)
        if status == "completed":
            return self._completed_view(upload_id)  # 幂等
        self.s3_finalize(upload_id=upload_id)
        return self._verify_and_finalize(upload_id=upload_id)

    def mark_completing(self, *, user_id: str, upload_id: str) -> str:
        """事务1:校验并进入 completing;返回进入前的目标状态。"""
        with self.sf() as s, s.begin():
            session = s.execute(
                select(UploadSessionRow).where(UploadSessionRow.id == upload_id)
                .with_for_update()
            ).scalar_one_or_none()
            if session is None or str(session.user_id) != user_id:
                raise ApiError("DOC_6003")
            if session.status == "completed":
                return "completed"
            if session.status == "completing":
                return "completing"  # 并发/崩溃重入:继续走 finalize
            if session.status != "active":
                raise ApiError("DOC_6003")
            parts = s.execute(select(UploadPart).where(
                UploadPart.upload_id == session.id)).scalars().all()
            missing = sorted(p.part_no for p in parts if p.status != "uploaded")
            if missing:
                raise ApiError("UPL_2102", detail={"missing": missing})
            session.status = "completing"
            return "completing"

    def s3_finalize(self, *, upload_id: str) -> None:
        """事务外:S3 合并,幂等可重放。

        - 成功或"NoSuchUpload 且对象已存在"(上次已合并)→ 返回;
        - ETag 无效等被 S3 拒绝 → 独立事务回 active,抛 UPL_2103(分片保留重传)。
        """
        with self.sf() as s:
            session = s.get(UploadSessionRow, upload_id)
            parts = s.execute(select(UploadPart).where(
                UploadPart.upload_id == upload_id).order_by(UploadPart.part_no)
            ).scalars().all()
            key, s3_id = session.storage_key, session.s3_upload_id
        try:
            self.store.complete_multipart_upload(
                key, s3_id, [{"PartNumber": p.part_no, "ETag": p.etag} for p in parts])
        except Exception as e:
            if self._object_exists(key):
                return  # 上次调用已合并成功(崩溃后重放/并发第二方)
            with self.sf() as s, s.begin():
                row = s.get(UploadSessionRow, upload_id, with_for_update=True)
                if row.status == "completing":
                    row.status = "active"  # 分片记录保留,客户端重传后再试
            raise ApiError("UPL_2103", detail={"reason": str(e)[:200]})

    def _verify_and_finalize(self, *, upload_id: str) -> dict:
        """事务2:对象校验 → 落账。校验失败先持久化修复动作再抛错。"""
        with self.sf() as s:
            session = s.get(UploadSessionRow, upload_id)
            key = session.storage_key
            expected_size, expected_sha = session.size_bytes, session.sha256
        actual_size = self.store.head_size(key)
        actual_sha = self.store.stream_sha256(key)
        if actual_size != expected_size or actual_sha != expected_sha:
            self._reopen(upload_id, reason="hash/size mismatch")
            raise ApiError("UPL_2103", detail={
                "expected_sha256": expected_sha, "actual_sha256": actual_sha})

        with self.sf() as s, s.begin():
            session = s.get(UploadSessionRow, upload_id, with_for_update=True)
            if session.status == "completed":
                pass  # 并发方已落账
            else:
                asset = MediaAsset(
                    id=str(uuid.uuid4()), user_id=str(session.user_id),
                    sha256=session.sha256, storage_key=session.storage_key,
                    size_bytes=session.size_bytes,
                )
                s.add(asset)
                session.status = "completed"
                rec = s.get(Recording, session.recording_id)
                rec.cloud_status = "uploaded"
                rec.media_asset_id = asset.id
                s.flush()
        return self._completed_view(upload_id)

    def _reopen(self, upload_id: str, *, reason: str) -> None:
        """修复动作独立持久化(核心修复):删对象 → 开新 multipart →
        新 ID 落库 + 分片重置 + 回 active,全部在自己的事务中提交。
        S3 先行、DB 后写:两者之间被杀 → completing + 对象缺失,
        reconcile() 会走同一 _reopen 路径,幂等。
        """
        with self.sf() as s:
            session = s.get(UploadSessionRow, upload_id)
            key = session.storage_key
        if self._object_exists(key):
            self.store.delete_object(key)
        new_s3_id = self.store.create_multipart_upload(key)
        with self.sf() as s, s.begin():
            session = s.get(UploadSessionRow, upload_id, with_for_update=True)
            session.s3_upload_id = new_s3_id
            session.status = "active"
            for p in s.execute(select(UploadPart).where(
                    UploadPart.upload_id == upload_id)).scalars().all():
                p.status = "pending"
                p.etag = None

    def reconcile(self) -> int:
        """Reconciler:恢复停留在 completing 的会话(进程被杀/落账失败)。

        - 对象已存在 → 继续校验并落账(或触发 _reopen);
        - 对象不存在 → multipart 已死,_reopen 重开可续传。
        cron 周期调用;返回处理数。
        """
        with self.sf() as s:
            stuck = s.execute(select(UploadSessionRow.id).where(
                UploadSessionRow.status == "completing")).scalars().all()
        handled = 0
        for upload_id in stuck:
            try:
                if self._object_exists_for(upload_id):
                    self._verify_and_finalize(upload_id=upload_id)
                else:
                    self._reopen(upload_id, reason="object missing after crash")
                handled += 1
            except ApiError:
                handled += 1  # 修复动作已持久化(如 _reopen),留待客户端续传
        return handled

    def _object_exists_for(self, upload_id: str) -> bool:
        with self.sf() as s:
            key = s.get(UploadSessionRow, upload_id).storage_key
        return self._object_exists(key)

    def _object_exists(self, key: str) -> bool:
        try:
            self.store.head_size(key)
            return True
        except Exception:
            return False

    def _completed_view(self, upload_id: str) -> dict:
        with self.sf() as s:
            session = s.get(UploadSessionRow, upload_id)
            rec = s.get(Recording, session.recording_id)
            return {"completed": True, "media_asset_id": str(rec.media_asset_id)}

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
