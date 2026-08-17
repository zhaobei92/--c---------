"""DB Worker:消费 Redis Streams,推进 PostgreSQL 中的转写任务。

幂等:同一 job 消息重复投递时,终态任务直接 ACK,不重复处理、不重复扣费/冲正。
毒消息(job 不存在/负载损坏)在投递次数超限后进入 DLQ。
阶段函数可注入:真实 ASR/分离/摘要在阶段3 接入;默认桩即时成功,
Mock AI Provider(黄金流程用)注入固定转写与摘要。
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Callable

from sqlalchemy.orm import Session, sessionmaker

from ..models.tables import NotificationJob, TranscriptionJob
from ..services.entitlement_service import EntitlementService
from ..services.job_state_machine import (
    JobState, JobStatus, fail_or_retry, transition,
)
from ..services.queue import TOPIC_TRANSCRIBE
from ..services.redis_queue import RedisStreamsQueue, StreamMessage
from .pipeline import StageError
from ..db.ledger import SqlLedgerStore

# DbStage: (session, job_row) -> None;失败抛 StageError(error_code)
DbStage = Callable[[Session, TranscriptionJob], None]


def _noop_stage(s: Session, job: TranscriptionJob) -> None:
    """空桩:纯基础设施测试用,不产出转写内容。"""


DEFAULT_STAGES: list[tuple[JobStatus, DbStage]] = [
    (JobStatus.PREPROCESSING, _noop_stage),
    (JobStatus.TRANSCRIBING, _noop_stage),
    (JobStatus.DIARIZING, _noop_stage),
    (JobStatus.SUMMARIZING, _noop_stage),
]


def mock_ai_stages() -> list[tuple[JobStatus, DbStage]]:
    """Mock AI Provider 阶段:写入真实 speakers/segments/summaries(Demo Mode)。"""
    from ..services.mock_ai import write_mock_summary, write_mock_transcript

    return [
        (JobStatus.PREPROCESSING, _noop_stage),
        (JobStatus.TRANSCRIBING, lambda s, j: write_mock_transcript(s, j)),
        (JobStatus.DIARIZING, _noop_stage),  # mock 转写阶段已含说话人
        (JobStatus.SUMMARIZING, lambda s, j: write_mock_summary(s, j)),
    ]


def stages_for(provider: str) -> list[tuple[JobStatus, DbStage]]:
    """按配置选择阶段实现:mock(Demo/审核)| noop(基础设施测试)。
    真实 ASR/LLM 供应商接入时在此登记。"""
    if provider == "mock":
        return mock_ai_stages()
    return DEFAULT_STAGES

MAX_DELIVERIES = 3


def process_message(session_factory: sessionmaker[Session],
                    msg: StreamMessage,
                    stages: list[tuple[JobStatus, DbStage]] | None = None) -> str:
    """处理一条消息;返回结果:completed / failed / skipped(幂等)/ missing。"""
    stages = stages or DEFAULT_STAGES
    job_id = msg.payload.get("job_id")
    if not job_id:
        return "missing"
    with session_factory() as s, s.begin():
        job = s.get(TranscriptionJob, job_id, with_for_update=True)
        if job is None:
            return "missing"
        if job.status in (JobStatus.COMPLETED.value, JobStatus.FAILED.value):
            return "skipped"  # 幂等:重复投递不重复处理

        st = JobState(status=JobStatus(job.status), retry_count=job.retry_count)
        if st.status is JobStatus.WAITING:
            transition(st, JobStatus.UPLOADING)
        while True:
            try:
                for target, stage in stages:
                    if target is JobStatus.DIARIZING and not job.diarize:
                        continue
                    if st.status is not target:
                        transition(st, target)
                    job.status = st.status.value
                    stage(s, job)
                transition(st, JobStatus.COMPLETED)
                job.status = st.status.value
                job.retry_count = st.retry_count
                job.error_code = None
                job.completed_at = datetime.now(timezone.utc)
                _notify(s, job, "job_completed")
                return "completed"
            except StageError as e:
                fail_or_retry(st, e.error_code)
                job.retry_count = st.retry_count
                job.error_code = st.error_code
                if st.status is JobStatus.FAILED:
                    job.status = st.status.value
                    _refund_current_generation(s, job)
                    _notify(s, job, "job_failed")
                    return "failed"
                transition(st, JobStatus.PREPROCESSING)  # 重入处理链起点


def run_worker_once(session_factory: sessionmaker[Session],
                    queue: RedisStreamsQueue, consumer: str = "w1",
                    stages: list[tuple[JobStatus, DbStage]] | None = None,
                    claim_idle_ms: int | None = None) -> dict[str, int]:
    """消费一轮(含可选的崩溃恢复重领);返回统计。"""
    stats = {"completed": 0, "failed": 0, "skipped": 0, "dlq": 0}
    messages = queue.read(TOPIC_TRANSCRIBE, consumer, count=50)
    if claim_idle_ms is not None:
        messages += queue.autoclaim(TOPIC_TRANSCRIBE, consumer,
                                    min_idle_ms=claim_idle_ms, count=50)
    for msg in messages:
        result = process_message(session_factory, msg, stages)
        if result == "missing":
            if queue.delivery_count(TOPIC_TRANSCRIBE, msg.id) >= MAX_DELIVERIES:
                queue.move_to_dlq(TOPIC_TRANSCRIBE, msg, reason="job missing")
                stats["dlq"] += 1
            # 未超限:不 ACK,留待重投(job 行可能因主从延迟尚不可见)
            continue
        queue.ack(TOPIC_TRANSCRIBE, msg.id)
        stats[result] += 1
    return stats


def _refund_current_generation(s: Session, job: TranscriptionJob) -> None:
    store = SqlLedgerStore(s)
    gens = [e.generation for e in store.entries_for(str(job.user_id))
            if e.job_id == str(job.id) and e.reason == "consume"]
    gen = max(gens) if gens else 0
    try:
        EntitlementService(store).refund_job(str(job.user_id), str(job.id),
                                             generation=gen)
    except Exception:
        pass  # DuplicateOperation:已冲正


def _notify(s: Session, job: TranscriptionJob, kind: str) -> None:
    s.add(NotificationJob(
        user_id=job.user_id, channel="inapp", type=kind,
        payload={"job_id": str(job.id), "recording_id": str(job.recording_id)},
    ))
