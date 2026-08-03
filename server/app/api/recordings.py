from __future__ import annotations

import uuid
from datetime import datetime

from fastapi import APIRouter
from pydantic import BaseModel

from ..core.errors import ApiError
from .deps import CurrentUser, state

router = APIRouter(prefix="/v1", tags=["recordings"])


class RecordingIn(BaseModel):
    title: str
    source: str = "device"  # device / phone
    device_sn: str | None = None
    device_file_id: str | None = None
    duration_ms: int
    sha256: str
    size_bytes: int
    recorded_at: datetime | None = None
    mode: str | None = None


@router.post("/recordings")
def create_recording(body: RecordingIn, user_id: str = CurrentUser):
    # 验收红线:重复文件率 0 —— (user, sha256) 判重,返回已有记录
    for rec in state.recordings.values():
        if rec["user_id"] == user_id and rec["sha256"] == body.sha256 and not rec.get("deleted"):
            return {**rec, "deduplicated": True}
    rec = {
        "id": str(uuid.uuid4()), "user_id": user_id, **body.model_dump(),
        "local_status": "synced", "cloud_status": "none", "deduplicated": False,
    }
    state.recordings[rec["id"]] = rec
    return rec


@router.get("/recordings")
def list_recordings(user_id: str = CurrentUser, page: int = 1, page_size: int = 20):
    items = [r for r in state.recordings.values() if r["user_id"] == user_id and not r.get("deleted")]
    start = (page - 1) * page_size
    return {"total": len(items), "page": page, "page_size": page_size,
            "items": items[start:start + page_size]}


@router.get("/recordings/{rec_id}")
def get_recording(rec_id: str, user_id: str = CurrentUser):
    return _owned(rec_id, user_id)


class RecordingPatch(BaseModel):
    title: str | None = None
    folder_id: str | None = None
    tag_ids: list[str] | None = None
    retention_days: int | None = None


@router.patch("/recordings/{rec_id}")
def patch_recording(rec_id: str, body: RecordingPatch, user_id: str = CurrentUser):
    rec = _owned(rec_id, user_id)
    rec.update({k: v for k, v in body.model_dump().items() if v is not None})
    return rec


@router.delete("/recordings/{rec_id}")
def delete_recording(rec_id: str, user_id: str = CurrentUser):
    rec = _owned(rec_id, user_id)
    rec["deleted"] = True
    # 生产:入 TOPIC_DELETE 队列,级联删除对象存储、转写、摘要、搜索索引,写 audit_logs
    state.audit.append({"actor_type": "user", "actor_id": user_id,
                       "action": "delete_recording", "target_id": rec_id})
    return {"deleted": True}


def _owned(rec_id: str, user_id: str) -> dict:
    rec = state.recordings.get(rec_id)
    if rec is None or rec.get("deleted"):
        raise ApiError("DOC_6003")
    if rec["user_id"] != user_id:
        raise ApiError("DOC_6003")  # 数据隔离:不泄露他人资源存在性
    return rec
