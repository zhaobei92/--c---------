from __future__ import annotations

import math
import uuid

from fastapi import APIRouter
from pydantic import BaseModel

from ..core.errors import ApiError
from ..services.entitlement_service import DuplicateOperation, InsufficientMinutes
from ..services.job_state_machine import JobState, JobStatus, transition
from ..services.queue import TOPIC_TRANSCRIBE
from .deps import CurrentUser, state

router = APIRouter(prefix="/v1", tags=["jobs"])


class JobIn(BaseModel):
    recording_id: str
    language_hint: str | None = None
    diarize: bool = True
    summary_template_id: str | None = None


@router.post("/jobs")
def create_job(body: JobIn, user_id: str = CurrentUser):
    rec = state.recordings.get(body.recording_id)
    if rec is None or rec["user_id"] != user_id or rec.get("deleted"):
        raise ApiError("DOC_6003")

    # 同 recording 已有成功任务 → 复用结果,不重复扣费
    for job in state.jobs.values():
        if job["recording_id"] == body.recording_id and job["state"].status is JobStatus.COMPLETED:
            return {**_view(job), "deduplicated": True}
    # 未终态任务存在 → 拒绝重复提交
    for job in state.jobs.values():
        if (job["recording_id"] == body.recording_id
                and job["state"].status not in (JobStatus.COMPLETED, JobStatus.FAILED)):
            return {**_view(job), "deduplicated": True}

    minutes = max(1, math.ceil(rec["duration_ms"] / 60000))
    job_id = str(uuid.uuid4())
    try:
        state.entitlements.consume(user_id, minutes, job_id=job_id)
    except InsufficientMinutes as e:
        raise ApiError("ENT_3001", detail={"required": e.required, "available": e.available})
    except DuplicateOperation:
        raise ApiError("ORD_5003")

    job = {
        "id": job_id, "user_id": user_id, "recording_id": body.recording_id,
        "language_hint": body.language_hint, "diarize": body.diarize,
        "minutes_charged": minutes, "state": JobState(),
    }
    state.jobs[job_id] = job
    state.queue.enqueue(TOPIC_TRANSCRIBE, {"job_id": job_id})
    return {**_view(job), "deduplicated": False}


@router.get("/jobs/{job_id}")
def get_job(job_id: str, user_id: str = CurrentUser):
    return _view(_owned(job_id, user_id))


@router.post("/jobs/{job_id}/retry")
def retry_job(job_id: str, user_id: str = CurrentUser):
    job = _owned(job_id, user_id)
    st: JobState = job["state"]
    if st.status is not JobStatus.FAILED:
        raise ApiError("SYS_9004", message="only failed jobs can be retried")
    # 失败重试不重复扣费:重建状态机,保留 minutes_charged
    job["state"] = JobState()
    state.queue.enqueue(TOPIC_TRANSCRIBE, {"job_id": job_id})
    return _view(job)


def _owned(job_id: str, user_id: str) -> dict:
    job = state.jobs.get(job_id)
    if job is None or job["user_id"] != user_id:
        raise ApiError("DOC_6003")
    return job


def _view(job: dict) -> dict:
    st: JobState = job["state"]
    return {
        "id": job["id"], "recording_id": job["recording_id"],
        "status": st.status.value, "retry_count": st.retry_count,
        "error_code": st.error_code, "minutes_charged": job["minutes_charged"],
    }
