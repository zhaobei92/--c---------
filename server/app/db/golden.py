"""黄金主链的 PostgreSQL 实现:用户 → 录音 → 上传 → 权益 → AI 任务 → Outbox。

核心事务(P0-5 正式实现,替代补偿式内存演示):

    BEGIN
      SELECT recording FOR UPDATE          -- 锁录音,串行化同录音的并发请求
      校验归属/删除/上传状态
      查询活跃任务(存在即幂等返回)
      SELECT entitlements FOR UPDATE       -- 锁权益桶
      写 usage_ledger 扣费流水(EntitlementService 语义复用)
      INSERT transcription_jobs
      INSERT outbox_events
    COMMIT

唯一索引兜底:uq_jobs_active(同录音仅一个未终态任务)与
usage_ledger.idempotency_key(重复扣费 = 0)在竞态窗口下由数据库最终裁决。
"""

from __future__ import annotations

import math
import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from ..core.config import settings
from ..core.errors import ApiError
from ..models.tables import (
    OutboxEvent, Recording, TranscriptionJob, User,
)
from ..services.entitlement_service import (
    DuplicateOperation, EntitlementService, InsufficientMinutes,
)
from ..services.job_state_machine import JobStatus
from ..services.queue import TOPIC_TRANSCRIBE
from .ledger import SqlLedgerStore

TERMINAL = (JobStatus.COMPLETED.value, JobStatus.FAILED.value)


def _now() -> datetime:
    return datetime.now(timezone.utc)


class GoldenChainDb:
    """黄金主链仓储门面。每个方法自管事务(commit/rollback)。"""

    def __init__(self, session_factory: sessionmaker[Session]):
        self.session_factory = session_factory

    # ---------------- 用户

    def ensure_user(self, email: str) -> tuple[str, bool]:
        with self.session_factory() as s, s.begin():
            row = s.execute(select(User).where(User.email == email)).scalar_one_or_none()
            if row is not None:
                return str(row.id), False
            user_id = str(uuid.uuid4())
            s.add(User(id=user_id, email=email, nickname=email.split("@")[0]))
            s.flush()
            ents = EntitlementService(SqlLedgerStore(s))
            ents.grant(user_id, "free_monthly", settings.free_monthly_minutes,
                       source_type="system", idempotency_key=f"signup:{user_id}")
            return user_id, True

    def update_user(self, user_id: str, changes: dict) -> dict:
        allowed = {"nickname", "language", "region", "audio_retention_days"}
        with self.session_factory() as s, s.begin():
            row = s.get(User, user_id)
            for k, v in changes.items():
                if k in allowed:
                    setattr(row, k, v)
        return self.get_user(user_id)

    def get_user(self, user_id: str) -> dict | None:
        with self.session_factory() as s:
            row = s.get(User, user_id)
            if row is None:
                return None
            return {"id": str(row.id), "email": row.email, "nickname": row.nickname,
                    "region": row.region, "language": row.language,
                    "audio_retention_days": row.audio_retention_days}

    # ---------------- 录音

    def create_recording(self, user_id: str, data: dict) -> dict:
        with self.session_factory() as s, s.begin():
            existing = s.execute(
                select(Recording).where(
                    Recording.user_id == user_id,
                    Recording.sha256 == data["sha256"],
                    Recording.deleted_at.is_(None),
                )
            ).scalar_one_or_none()
            if existing is not None:
                return {**self._rec_view(existing), "deduplicated": True}
            row = Recording(
                id=str(uuid.uuid4()), user_id=user_id, title=data["title"],
                source=data.get("source", "device"), device_sn=data.get("device_sn"),
                device_file_id=data.get("device_file_id"), mode=data.get("mode"),
                duration_ms=data["duration_ms"], size_bytes=data["size_bytes"],
                sha256=data["sha256"], recorded_at=data.get("recorded_at"),
                local_status="synced", cloud_status="none",
            )
            s.add(row)
            s.flush()
            return {**self._rec_view(row), "deduplicated": False}

    def get_recording(self, user_id: str, rec_id: str) -> dict | None:
        with self.session_factory() as s:
            row = s.get(Recording, rec_id)
            if row is None or str(row.user_id) != user_id or row.deleted_at is not None:
                return None
            return self._rec_view(row)

    def list_recordings(self, user_id: str) -> list[dict]:
        with self.session_factory() as s:
            rows = s.execute(
                select(Recording).where(Recording.user_id == user_id,
                                        Recording.deleted_at.is_(None))
                .order_by(Recording.created_at.desc())
            ).scalars().all()
            return [self._rec_view(r) for r in rows]

    def delete_recording(self, user_id: str, rec_id: str) -> bool:
        with self.session_factory() as s, s.begin():
            row = s.get(Recording, rec_id)
            if row is None or str(row.user_id) != user_id or row.deleted_at is not None:
                return False
            row.deleted_at = _now()
            # 生产:入 TOPIC_DELETE 级联清对象存储/转写/摘要/搜索索引 + audit_logs
            return True

    def list_notifications(self, user_id: str) -> list[dict]:
        from ..models.tables import NotificationJob
        with self.session_factory() as s:
            rows = s.execute(
                select(NotificationJob).where(NotificationJob.user_id == user_id)
                .order_by(NotificationJob.scheduled_at.desc())
            ).scalars().all()
            return [{"type": r.type, **(r.payload or {})} for r in rows]

    def mark_uploaded(self, rec_id: str, media_asset_id: str) -> None:
        with self.session_factory() as s, s.begin():
            row = s.get(Recording, rec_id)
            if row is not None:
                row.cloud_status = "uploaded"
                row.media_asset_id = media_asset_id

    @staticmethod
    def _rec_view(r: Recording) -> dict:
        return {
            "id": str(r.id), "user_id": str(r.user_id), "title": r.title,
            "source": r.source, "device_sn": r.device_sn,
            "duration_ms": r.duration_ms, "size_bytes": r.size_bytes,
            "sha256": r.sha256, "local_status": r.local_status,
            "cloud_status": r.cloud_status,
            "media_asset_id": str(r.media_asset_id) if r.media_asset_id else None,
        }

    # ---------------- 权益

    def balances(self, user_id: str) -> dict[str, int]:
        with self.session_factory() as s:
            return EntitlementService(SqlLedgerStore(s)).bucket_balances(user_id)

    def usage_entries(self, user_id: str) -> list[dict]:
        with self.session_factory() as s:
            entries = SqlLedgerStore(s).entries_for(user_id)
            entries.sort(key=lambda e: e.created_at, reverse=True)
            return [
                {"delta_minutes": e.delta_minutes, "reason": e.reason,
                 "job_id": e.job_id, "generation": e.generation,
                 "order_id": e.order_id, "created_at": e.created_at.isoformat()}
                for e in entries
            ]

    def grant(self, user_id: str, bucket: str, minutes: int, *,
              source_type: str, idempotency_key: str | None = None) -> str:
        with self.session_factory() as s, s.begin():
            ent = EntitlementService(SqlLedgerStore(s)).grant(
                user_id, bucket, minutes, source_type=source_type,
                idempotency_key=idempotency_key)
            return ent.id

    # ---------------- AI 任务(核心事务)

    def create_job(self, user_id: str, recording_id: str, *,
                   language_hint: str | None = None, diarize: bool = True) -> dict:
        try:
            with self.session_factory() as s, s.begin():
                rec = s.execute(
                    select(Recording).where(Recording.id == recording_id)
                    .with_for_update()
                ).scalar_one_or_none()
                if rec is None or str(rec.user_id) != user_id or rec.deleted_at is not None:
                    raise ApiError("DOC_6003")

                # 已完成任务 → 复用结果;未终态任务 → 幂等返回(仅 failed 允许新建)
                reusable = s.execute(
                    select(TranscriptionJob).where(
                        TranscriptionJob.recording_id == recording_id,
                        TranscriptionJob.status != JobStatus.FAILED.value,
                    )
                ).scalars().first()
                if reusable is not None:
                    return {**self._job_view(reusable), "deduplicated": True}

                minutes = max(1, math.ceil((rec.duration_ms or 60000) / 60000))
                job_id = str(uuid.uuid4())
                # 先插 job 行(ledger.job_id 外键指向它),再锁权益扣费;
                # 任一步失败整个事务回滚,不留下任何一半状态。
                job = TranscriptionJob(
                    id=job_id, recording_id=recording_id, user_id=user_id,
                    status=JobStatus.WAITING.value, language_hint=language_hint,
                    diarize=diarize, minutes_charged=minutes,
                )
                s.add(job)
                s.flush()
                store = SqlLedgerStore(s)
                store.lock_user_rows(user_id)
                EntitlementService(store).consume(user_id, minutes, job_id=job_id)
                s.add(OutboxEvent(
                    id=str(uuid.uuid4()), topic=TOPIC_TRANSCRIBE,
                    payload={"job_id": job_id}, next_attempt_at=_now(),
                ))
                s.flush()
                return {**self._job_view(job), "deduplicated": False}
        except InsufficientMinutes as e:
            raise ApiError("ENT_3001",
                           detail={"required": e.required, "available": e.available})
        except IntegrityError:
            # 并发竞态被 uq_jobs_active 兜底:回滚后返回已存在的任务
            with self.session_factory() as s:
                job = s.execute(
                    select(TranscriptionJob).where(
                        TranscriptionJob.recording_id == recording_id,
                        TranscriptionJob.status.notin_(TERMINAL),
                    )
                ).scalars().first()
                if job is None:
                    raise ApiError("SYS_9005")
                return {**self._job_view(job), "deduplicated": True}

    def get_job(self, user_id: str, job_id: str) -> dict | None:
        with self.session_factory() as s:
            job = s.get(TranscriptionJob, job_id)
            if job is None or str(job.user_id) != user_id:
                return None
            return self._job_view(job)

    def retry_job(self, user_id: str, job_id: str) -> dict:
        """人工重试:同一事务内校验退款状态并按新 generation 重新扣费。"""
        try:
            with self.session_factory() as s, s.begin():
                job = s.execute(
                    select(TranscriptionJob).where(TranscriptionJob.id == job_id)
                    .with_for_update()
                ).scalar_one_or_none()
                if job is None or str(job.user_id) != user_id:
                    raise ApiError("DOC_6003")
                if job.status != JobStatus.FAILED.value:
                    raise ApiError("SYS_9004", message="only failed jobs can be retried")

                store = SqlLedgerStore(s)
                gen = self._current_generation(store, user_id, job_id)
                refunded = any(
                    e.job_id == job_id and e.generation == gen and e.reason == "refund"
                    for e in store.entries_for(user_id)
                )
                if refunded:
                    store.lock_user_rows(user_id)
                    EntitlementService(store).consume(
                        user_id, job.minutes_charged, job_id=job_id, generation=gen + 1)
                job.status = JobStatus.WAITING.value
                job.error_code = None
                job.retry_count = 0
                s.add(OutboxEvent(
                    id=str(uuid.uuid4()), topic=TOPIC_TRANSCRIBE,
                    payload={"job_id": job_id}, next_attempt_at=_now(),
                ))
                s.flush()
                return self._job_view(job)
        except InsufficientMinutes as e:
            raise ApiError("ENT_3001",
                           detail={"required": e.required, "available": e.available})

    @staticmethod
    def _current_generation(store: SqlLedgerStore, user_id: str, job_id: str) -> int:
        gens = [e.generation for e in store.entries_for(user_id)
                if e.job_id == job_id and e.reason == "consume"]
        return max(gens) if gens else 0

    @staticmethod
    def _job_view(job: TranscriptionJob) -> dict:
        return {
            "id": str(job.id), "recording_id": str(job.recording_id),
            "status": job.status, "retry_count": job.retry_count,
            "error_code": job.error_code, "minutes_charged": job.minutes_charged,
        }
