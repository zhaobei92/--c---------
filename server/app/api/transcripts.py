"""转写结果读取与说话人编辑(Phase 3;DB 后端)。

契约见 docs/04-api-spec.md §6。这些接口只在 DB 模式下有数据来源;
内存模式返回 SYS_9004(单元测试不覆盖此路径,Demo/生产均为 DB 模式)。
"""

from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel

from ..core.errors import ApiError
from .deps import CurrentUser, state

router = APIRouter(prefix="/v1", tags=["transcripts"])


def _db():
    if state.db is None:
        raise ApiError("SYS_9004", message="transcript endpoints require db backend")
    return state.db


@router.get("/recordings/{rec_id}/transcript")
def get_transcript(rec_id: str, user_id: str = CurrentUser):
    db = _db()
    if db.get_recording(user_id, rec_id) is None:
        raise ApiError("DOC_6003")
    return {"segments": db.get_transcript(rec_id)}


@router.get("/recordings/{rec_id}/speakers")
def get_speakers(rec_id: str, user_id: str = CurrentUser):
    db = _db()
    if db.get_recording(user_id, rec_id) is None:
        raise ApiError("DOC_6003")
    return {"speakers": db.get_speakers(rec_id)}


class SpeakerPatch(BaseModel):
    speaker_id: str
    display_name: str


@router.patch("/recordings/{rec_id}/speakers")
def rename_speaker(rec_id: str, body: SpeakerPatch, user_id: str = CurrentUser):
    """改名全篇统一生效:segments 经 speaker_id 关联,读取侧自然一致。"""
    db = _db()
    if db.get_recording(user_id, rec_id) is None:
        raise ApiError("DOC_6003")
    if not db.rename_speaker(rec_id, body.speaker_id, body.display_name):
        raise ApiError("DOC_6003")
    return {"speakers": db.get_speakers(rec_id)}


@router.get("/recordings/{rec_id}/summary")
def get_summary(rec_id: str, user_id: str = CurrentUser):
    db = _db()
    if db.get_recording(user_id, rec_id) is None:
        raise ApiError("DOC_6003")
    summary = db.get_summary(rec_id)
    if summary is None:
        raise ApiError("DOC_6003", message="summary not ready")
    return summary
