from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel

from ..core.errors import ApiError
from ..services.upload_service import FileTooLarge, HashMismatch, MissingParts, UploadError
from .deps import CurrentUser, state

router = APIRouter(prefix="/v1", tags=["uploads"])


class InitIn(BaseModel):
    recording_id: str
    size_bytes: int
    sha256: str
    part_size: int | None = None


@router.post("/uploads/init")
def init_upload(body: InitIn, user_id: str = CurrentUser):
    if state.uploads_pg is not None:
        kwargs = dict(user_id=user_id, recording_id=body.recording_id,
                      size_bytes=body.size_bytes, sha256=body.sha256)
        if body.part_size:
            kwargs["part_size"] = body.part_size
        return state.uploads_pg.init_upload(**kwargs)
    # P0-3 越权修复:recording 必须存在、属于当前用户、未删除;
    # 元数据(size/sha256)必须与登记值一致,防止会话指向他人/伪造内容。
    rec = state.recordings.get(body.recording_id)
    if rec is None or rec.get("deleted") or rec["user_id"] != user_id:
        raise ApiError("DOC_6003")  # 不泄露他人资源存在性
    if rec["sha256"] != body.sha256 or rec["size_bytes"] != body.size_bytes:
        raise ApiError("SYS_9004", detail={
            "reason": "metadata mismatch with registered recording"})
    try:
        kwargs = dict(user_id=user_id, recording_id=body.recording_id,
                      size_bytes=body.size_bytes, sha256=body.sha256)
        if body.part_size:
            kwargs["part_size"] = body.part_size
        result = state.uploads.init_upload(**kwargs)
    except FileTooLarge:
        raise ApiError("UPL_2002", detail={"size_bytes": body.size_bytes})
    if result.deduplicated:
        rec["cloud_status"] = "uploaded"
        rec["media_asset_id"] = result.media_asset_id
        return {"deduplicated": True, "media_asset_id": result.media_asset_id}
    s = result.session
    return {
        "deduplicated": False, "upload_id": s.upload_id, "part_size": s.part_size,
        "parts": [{"part_no": p.part_no, "put_url": p.put_url} for p in s.parts.values()],
    }


class PartIn(BaseModel):
    etag: str
    size_bytes: int


@router.post("/uploads/{upload_id}/parts/{part_no}/complete")
def complete_part(upload_id: str, part_no: int, body: PartIn, user_id: str = CurrentUser):
    if state.uploads_pg is not None:
        return state.uploads_pg.register_part(
            user_id=user_id, upload_id=upload_id, part_no=part_no,
            etag=body.etag, size_bytes=body.size_bytes)
    _owned_session(upload_id, user_id)
    try:
        part = state.uploads.register_part(upload_id, part_no, etag=body.etag,
                                           size_bytes=body.size_bytes)
    except UploadError as e:
        raise ApiError("UPL_2101", detail={"part_no": part_no, "reason": str(e)})
    return {"part_no": part.part_no, "status": part.status}


@router.get("/uploads/{upload_id}")
def upload_progress(upload_id: str, user_id: str = CurrentUser):
    if state.uploads_pg is not None:
        view = state.uploads_pg.progress(user_id=user_id, upload_id=upload_id)
        return {"upload_id": upload_id, "status": view["status"],
                "pending_parts": view["pending_parts"], "parts": view["parts"],
                "total_parts": view["total_parts"]}
    """断点续传契约:pending 分片附带重新签发的 put_url(旧预签名可能已过期)。"""
    s = _owned_session(upload_id, user_id)
    pending = state.uploads.pending_parts(upload_id)
    parts = [{"part_no": n, "put_url": state.uploads.reissue_put_url(upload_id, n)}
             for n in pending]
    return {"upload_id": upload_id, "status": s.status,
            "pending_parts": pending, "parts": parts, "total_parts": len(s.parts)}


@router.post("/uploads/{upload_id}/complete")
def complete_upload(upload_id: str, user_id: str = CurrentUser):
    if state.uploads_pg is not None:
        return state.uploads_pg.complete(user_id=user_id, upload_id=upload_id)
    _owned_session(upload_id, user_id)
    try:
        s = state.uploads.complete(upload_id)
    except MissingParts as e:
        raise ApiError("UPL_2102", detail={"missing": e.missing})
    except HashMismatch:
        raise ApiError("UPL_2103")
    rec = state.recordings.get(s.recording_id)
    if rec is not None:
        rec["cloud_status"] = "uploaded"
        rec["media_asset_id"] = s.media_asset_id
    return {"completed": True, "media_asset_id": s.media_asset_id}


@router.delete("/uploads/{upload_id}")
def abort_upload(upload_id: str, user_id: str = CurrentUser):
    if state.uploads_pg is not None:
        state.uploads_pg.abort(user_id=user_id, upload_id=upload_id)
        return {"aborted": True}
    _owned_session(upload_id, user_id)
    state.uploads.abort(upload_id)
    return {"aborted": True}


def _owned_session(upload_id: str, user_id: str):
    s = state.uploads.sessions.get(upload_id)
    if s is None or s.user_id != user_id:
        raise ApiError("UPL_2001", message="upload session not found")
    return s
