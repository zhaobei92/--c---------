"""SQLAlchemy 模型 — 24 张核心表。

权威 DDL 见 migrations/schema.sql(PostgreSQL 专有特性以其为准:citext、uuid[]、
partial unique index、GIN 全文索引)。本模型用可移植类型(String/JSON),
使单元测试可跑在 SQLite 上;生产迁移用 schema.sql / Alembic。
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    JSON, BigInteger, Boolean, Date, DateTime, Float, ForeignKey, Integer,
    SmallInteger, String, Text, UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def _uuid() -> str:
    return str(uuid.uuid4())


def _now() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class _PK:
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)


# ------------------------------------------------------------ 用户域

class User(_PK, Base):
    __tablename__ = "users"
    email: Mapped[str | None] = mapped_column(String(320), unique=True)
    password_hash: Mapped[str | None] = mapped_column(Text)
    nickname: Mapped[str | None] = mapped_column(String(80))
    region: Mapped[str] = mapped_column(String(8), default="CN")
    language: Mapped[str] = mapped_column(String(8), default="zh")
    audio_retention_days: Mapped[int | None] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(16), default="active")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now)


class UserIdentity(_PK, Base):
    __tablename__ = "user_identities"
    __table_args__ = (UniqueConstraint("provider", "provider_uid"),)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    provider: Mapped[str] = mapped_column(String(16))  # email / apple / google
    provider_uid: Mapped[str] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class ConsentLog(_PK, Base):
    __tablename__ = "consent_logs"
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    consent_type: Mapped[str] = mapped_column(String(40))
    granted: Mapped[bool] = mapped_column(Boolean)
    app_version: Mapped[str | None] = mapped_column(String(20))
    ip_hash: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class DeletionRequest(_PK, Base):
    __tablename__ = "deletion_requests"
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    scope: Mapped[str] = mapped_column(String(16), default="account")
    target_id: Mapped[str | None] = mapped_column(String(36))
    status: Mapped[str] = mapped_column(String(16), default="cooling")
    requested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    execute_after: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


# ------------------------------------------------------------ 设备域

class Device(_PK, Base):
    __tablename__ = "devices"
    sn: Mapped[str] = mapped_column(String(64), unique=True)
    model: Mapped[str] = mapped_column(String(40))
    firmware_version: Mapped[str | None] = mapped_column(String(20))
    device_key_enc: Mapped[str | None] = mapped_column(Text)
    first_activated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    warranty_until: Mapped[datetime | None] = mapped_column(Date)
    blacklisted: Mapped[bool] = mapped_column(Boolean, default=False)
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    battery: Mapped[int | None] = mapped_column(SmallInteger)
    storage_free_mb: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class DeviceBinding(_PK, Base):
    __tablename__ = "device_bindings"
    device_id: Mapped[str] = mapped_column(ForeignKey("devices.id"))
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    bound_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    unbound_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class FirmwareVersion(_PK, Base):
    __tablename__ = "firmware_versions"
    __table_args__ = (UniqueConstraint("model", "version"),)
    model: Mapped[str] = mapped_column(String(40))
    version: Mapped[str] = mapped_column(String(20))
    url: Mapped[str] = mapped_column(Text)
    sha256: Mapped[str] = mapped_column(String(64))
    signature: Mapped[str] = mapped_column(Text)
    min_battery: Mapped[int] = mapped_column(SmallInteger, default=30)
    min_app_version: Mapped[str | None] = mapped_column(String(20))
    force_update: Mapped[bool] = mapped_column(Boolean, default=False)
    blacklisted: Mapped[bool] = mapped_column(Boolean, default=False)
    released_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


# ------------------------------------------------------------ 录音与文件域

class Folder(_PK, Base):
    __tablename__ = "folders"
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    name: Mapped[str] = mapped_column(String(120))
    parent_id: Mapped[str | None] = mapped_column(ForeignKey("folders.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class Tag(_PK, Base):
    __tablename__ = "tags"
    __table_args__ = (UniqueConstraint("user_id", "name"),)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    name: Mapped[str] = mapped_column(String(60))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class MediaAsset(_PK, Base):
    __tablename__ = "media_assets"
    __table_args__ = (UniqueConstraint("user_id", "sha256"),)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    sha256: Mapped[str] = mapped_column(String(64))
    storage_key: Mapped[str] = mapped_column(Text)
    size_bytes: Mapped[int] = mapped_column(BigInteger)
    mime: Mapped[str | None] = mapped_column(String(80))
    duration_ms: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class Recording(_PK, Base):
    __tablename__ = "recordings"
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    title: Mapped[str] = mapped_column(String(255))
    source: Mapped[str] = mapped_column(String(10), default="device")
    device_sn: Mapped[str | None] = mapped_column(String(64))
    device_file_id: Mapped[str | None] = mapped_column(String(128))
    mode: Mapped[str | None] = mapped_column(String(20))
    duration_ms: Mapped[int | None] = mapped_column(Integer)
    size_bytes: Mapped[int | None] = mapped_column(BigInteger)
    sha256: Mapped[str | None] = mapped_column(String(64))
    media_asset_id: Mapped[str | None] = mapped_column(ForeignKey("media_assets.id"))
    local_status: Mapped[str] = mapped_column(String(16), default="none")
    cloud_status: Mapped[str] = mapped_column(String(16), default="none")
    folder_id: Mapped[str | None] = mapped_column(ForeignKey("folders.id"))
    # PG 实际类型为 uuid[](库默认 '{}');模型不带默认值,插入时不写此列,
    # 避免 JSON 序列化与数组类型冲突。读写标签走专用 UPDATE(数组适配)。
    tag_ids: Mapped[list | None] = mapped_column(JSON, nullable=True)
    recorded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    retention_days: Mapped[int | None] = mapped_column(Integer)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now)


class UploadPart(_PK, Base):
    __tablename__ = "upload_parts"
    __table_args__ = (UniqueConstraint("upload_id", "part_no"),)
    upload_id: Mapped[str] = mapped_column(String(36))
    recording_id: Mapped[str] = mapped_column(ForeignKey("recordings.id", ondelete="CASCADE"))
    part_no: Mapped[int] = mapped_column(Integer)
    size_bytes: Mapped[int | None] = mapped_column(BigInteger)
    etag: Mapped[str | None] = mapped_column(String(128))
    sha256: Mapped[str | None] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(12), default="pending")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


# ------------------------------------------------------------ AI 域

class TranscriptionJob(_PK, Base):
    __tablename__ = "transcription_jobs"
    recording_id: Mapped[str] = mapped_column(ForeignKey("recordings.id"))
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    status: Mapped[str] = mapped_column(String(16), default="waiting")
    language_hint: Mapped[str | None] = mapped_column(String(8))
    diarize: Mapped[bool] = mapped_column(Boolean, default=True)
    template_id: Mapped[str | None] = mapped_column(String(36))
    error_code: Mapped[str | None] = mapped_column(String(12))
    retry_count: Mapped[int] = mapped_column(Integer, default=0)
    minutes_charged: Mapped[int] = mapped_column(Integer, default=0)
    asr_provider: Mapped[str | None] = mapped_column(String(40))
    cost_asr_cents: Mapped[int] = mapped_column(Integer, default=0)
    cost_diar_cents: Mapped[int] = mapped_column(Integer, default=0)
    cost_llm_cents: Mapped[int] = mapped_column(Integer, default=0)
    cost_storage_cents: Mapped[int] = mapped_column(Integer, default=0)
    cost_egress_cents: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Speaker(_PK, Base):
    __tablename__ = "speakers"
    __table_args__ = (UniqueConstraint("recording_id", "label"),)
    recording_id: Mapped[str] = mapped_column(ForeignKey("recordings.id", ondelete="CASCADE"))
    label: Mapped[str] = mapped_column(String(40))
    display_name: Mapped[str | None] = mapped_column(String(80))


class TranscriptSegment(_PK, Base):
    __tablename__ = "transcript_segments"
    __table_args__ = (UniqueConstraint("recording_id", "seq"),)
    job_id: Mapped[str] = mapped_column(ForeignKey("transcription_jobs.id", ondelete="CASCADE"))
    recording_id: Mapped[str] = mapped_column(ForeignKey("recordings.id", ondelete="CASCADE"))
    seq: Mapped[int] = mapped_column(Integer)
    start_ms: Mapped[int] = mapped_column(Integer)
    end_ms: Mapped[int] = mapped_column(Integer)
    speaker_id: Mapped[str | None] = mapped_column(ForeignKey("speakers.id"))
    language: Mapped[str | None] = mapped_column(String(8))
    text: Mapped[str] = mapped_column(Text)
    text_edited: Mapped[str | None] = mapped_column(Text)
    confidence: Mapped[float | None] = mapped_column(Float)


class SummaryTemplate(_PK, Base):
    __tablename__ = "summary_templates"
    key: Mapped[str] = mapped_column(String(40), unique=True)
    name_i18n: Mapped[dict] = mapped_column(JSON)
    prompt: Mapped[str] = mapped_column(Text)
    builtin: Mapped[bool] = mapped_column(Boolean, default=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class Summary(_PK, Base):
    __tablename__ = "summaries"
    recording_id: Mapped[str] = mapped_column(ForeignKey("recordings.id", ondelete="CASCADE"))
    job_id: Mapped[str | None] = mapped_column(ForeignKey("transcription_jobs.id"))
    template_id: Mapped[str | None] = mapped_column(ForeignKey("summary_templates.id"))
    content: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class Translation(_PK, Base):
    __tablename__ = "translations"
    __table_args__ = (UniqueConstraint("recording_id", "lang"),)
    recording_id: Mapped[str] = mapped_column(ForeignKey("recordings.id", ondelete="CASCADE"))
    lang: Mapped[str] = mapped_column(String(8))
    content: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


# ------------------------------------------------------------ 商业化域

class Subscription(_PK, Base):
    __tablename__ = "subscriptions"
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    platform: Mapped[str] = mapped_column(String(10))
    product_id: Mapped[str] = mapped_column(String(80))
    original_transaction_id: Mapped[str] = mapped_column(String(128), unique=True)
    status: Mapped[str] = mapped_column(String(12))
    auto_renew: Mapped[bool] = mapped_column(Boolean, default=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    last_verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class Entitlement(_PK, Base):
    __tablename__ = "entitlements"
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    bucket: Mapped[str] = mapped_column(String(16))
    minutes_granted: Mapped[int] = mapped_column(Integer)
    effective_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    source_type: Mapped[str] = mapped_column(String(16))
    source_id: Mapped[str | None] = mapped_column(String(36))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class UsageLedger(_PK, Base):
    __tablename__ = "usage_ledger"
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    entitlement_id: Mapped[str | None] = mapped_column(ForeignKey("entitlements.id"))
    delta_minutes: Mapped[int] = mapped_column(Integer)
    reason: Mapped[str] = mapped_column(String(12))  # grant/consume/refund/expire/adjust
    job_id: Mapped[str | None] = mapped_column(ForeignKey("transcription_jobs.id"))
    charge_generation: Mapped[int] = mapped_column(Integer, default=0)
    order_id: Mapped[str | None] = mapped_column(String(36))
    idempotency_key: Mapped[str | None] = mapped_column(String(120), unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class Order(_PK, Base):
    __tablename__ = "orders"
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    platform: Mapped[str] = mapped_column(String(10))
    product_id: Mapped[str] = mapped_column(String(80))
    transaction_id: Mapped[str | None] = mapped_column(String(128), unique=True)
    purchase_token: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(12), default="pending")
    amount_cents: Mapped[int | None] = mapped_column(Integer)
    currency: Mapped[str | None] = mapped_column(String(8))
    raw_payload: Mapped[dict | None] = mapped_column(JSON)
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


# ------------------------------------------------------------ 运营域

class AuditLog(_PK, Base):
    __tablename__ = "audit_logs"
    actor_type: Mapped[str] = mapped_column(String(10))
    actor_id: Mapped[str | None] = mapped_column(String(36))
    action: Mapped[str] = mapped_column(String(60))
    target_type: Mapped[str | None] = mapped_column(String(40))
    target_id: Mapped[str | None] = mapped_column(String(36))
    detail: Mapped[dict | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class OutboxEvent(_PK, Base):
    __tablename__ = "outbox_events"
    topic: Mapped[str] = mapped_column(String(60))
    payload: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    last_error: Mapped[str | None] = mapped_column(Text)
    next_attempt_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class UploadSessionRow(_PK, Base):
    """真实 S3 Multipart 会话(0002 迁移引入;API 重启后据此恢复上传)。"""

    __tablename__ = "upload_sessions"
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    recording_id: Mapped[str] = mapped_column(ForeignKey("recordings.id"))
    storage_key: Mapped[str] = mapped_column(Text)
    s3_upload_id: Mapped[str] = mapped_column(Text)
    size_bytes: Mapped[int] = mapped_column(BigInteger)
    part_size: Mapped[int] = mapped_column(Integer)
    sha256: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(12), default="active")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class NotificationJob(_PK, Base):
    __tablename__ = "notification_jobs"
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    channel: Mapped[str] = mapped_column(String(10))
    type: Mapped[str] = mapped_column(String(40))
    payload: Mapped[dict] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(10), default="pending")
    scheduled_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error: Mapped[str | None] = mapped_column(Text)


ALL_TABLES = [
    "users", "user_identities", "devices", "device_bindings", "firmware_versions",
    "recordings", "media_assets", "upload_parts", "transcription_jobs",
    "transcript_segments", "speakers", "summaries", "summary_templates",
    "translations", "folders", "tags", "subscriptions", "entitlements",
    "usage_ledger", "orders", "consent_logs", "audit_logs",
    "deletion_requests", "notification_jobs", "outbox_events", "upload_sessions",
]
