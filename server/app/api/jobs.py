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
    # P0-5:扣费 + 建任务 + 写 outbox 必须原子。生产实现为同一 PostgreSQL 事务
    # (锁录音→查活跃任务→锁权益桶→写 ledger→建 job→写 outbox_events→提交),
    # 由 Outbox Publisher 可靠投递到队列。内存骨架用补偿保持同等语义:
    # 任一步失败即冲正已扣分钟,不留下"扣了钱没任务"。
    try:
        state.entitlements.consume(user_id, minutes, job_id=job_id)
    except InsufficientMinutes as e:
        raise ApiError("ENT_3001", detail={"required": e.required, "available": e.available})
    except DuplicateOperation:
        raise ApiError("ORD_5003")
    try:
        job = {
            "id": job_id, "user_id": user_id, "recording_id": body.recording_id,
            "language_hint": body.language_hint, "diarize": body.diarize,
            "minutes_charged": minutes, "state": JobState(),
            "retry_generation": 0, "charge_ref": job_id,
        }
        state.jobs[job_id] = job
        state.outbox.append({"topic": TOPIC_TRANSCRIBE, "payload": {"job_id": job_id}})
    except Exception:
        state.jobs.pop(job_id, None)
        state.entitlements.refund_job(user_id, job_id)  # 补偿:扣费回滚
        raise
    return {**_view(job), "deduplicated": False}


@router.get("/jobs/{job_id}")
def get_job(job_id: str, user_id: str = CurrentUser):
    return _view(_owned(job_id, user_id))


@router.post("/jobs/{job_id}/retry")
def retry_job(job_id: str, user_id: str = CurrentUser):
    """人工重试计费规则(P0-6 修复):

    - 系统自动重试(worker 内 ≤3 次)不重复收费;
    - 终态失败时 worker 已把当代扣费冲正 → 人工重试开启新 generation,
      必须重新扣费(余额不足 ENT_3001 拒绝);
    - 若当代扣费未被冲正(冲正尚未发生的边缘情况),重试免费;
    - 各代扣费/冲正以 charge_ref = {job_id}#g{n} 关联原任务,流水可追溯。
    """
    job = _owned(job_id, user_id)
    st: JobState = job["state"]
    if st.status is not JobStatus.FAILED:
        raise ApiError("SYS_9004", message="only failed jobs can be retried")

    current_ref = job["charge_ref"]
    refunded = any(
        e.job_id == current_ref and e.reason == "refund"
        for e in state.entitlements.store.entries_for(user_id)
    )
    if refunded:
        generation = job["retry_generation"] + 1
        new_ref = f"{job_id}#g{generation}"
        try:
            state.entitlements.consume(user_id, job["minutes_charged"], job_id=new_ref)
        except InsufficientMinutes as e:
            raise ApiError("ENT_3001",
                           detail={"required": e.required, "available": e.available})
        job["retry_generation"] = generation
        job["charge_ref"] = new_ref

    job["state"] = JobState()
    state.outbox.append({"topic": TOPIC_TRANSCRIBE, "payload": {"job_id": job_id}})
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
        "retry_generation": job.get("retry_generation", 0),
    }
