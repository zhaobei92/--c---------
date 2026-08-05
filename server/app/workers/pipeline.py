"""AI 处理管线 Worker(十步管线,docs/02-architecture.md §4)。

阶段1 骨架:各阶段为可替换的 Stage 函数,真实 ASR/分离/摘要供应商在阶段3 接入。
Worker 从队列取 job,依状态机推进;任一阶段抛错走 fail_or_retry,
终态 failed 时对已扣分钟做 ledger 冲正。
"""

from __future__ import annotations

from typing import Callable

from ..api.deps import AppState
from ..services.job_state_machine import JobState, JobStatus, fail_or_retry, transition
from ..services.queue import TOPIC_TRANSCRIBE

# Stage: (job_dict, app_state) -> None,失败抛异常(异常需带 error_code 属性)
Stage = Callable[[dict, AppState], None]


class StageError(Exception):
    def __init__(self, error_code: str, detail: str = ""):
        self.error_code = error_code
        super().__init__(f"{error_code}: {detail}")


def stage_preprocess(job: dict, app: AppState) -> None:
    """标准化:单声道/固定采样率/归一化/静音裁剪/异常检测(FFmpeg,阶段3 接入)。"""


def stage_transcribe(job: dict, app: AppState) -> None:
    """VAD → 分段语言识别(5—20s)→ ASR(文本/时间戳/置信度/标点)。

    阶段3:接 ASR 适配层(多供应商路由);失败抛 StageError("JOB_4101"/"JOB_4102")。
    """


def stage_diarize(job: dict, app: AppState) -> None:
    """说话人分离 → speakers + segment.speaker_id;失败可降级(JOB_4103)。"""


def stage_summarize(job: dict, app: AppState) -> None:
    """文本后处理 + 摘要/待办(强制时间戳 evidence)+ 翻译层;失败 JOB_4104/4105。"""


PIPELINE: list[tuple[JobStatus, Stage]] = [
    (JobStatus.PREPROCESSING, stage_preprocess),
    (JobStatus.TRANSCRIBING, stage_transcribe),
    (JobStatus.DIARIZING, stage_diarize),
    (JobStatus.SUMMARIZING, stage_summarize),
]


def process_job(job: dict, app: AppState) -> JobState:
    """将一个任务从 waiting 推进到终态。

    失败语义:任一阶段抛 StageError → fail_or_retry;进入 retrying 后
    重入处理链起点(preprocessing)整链重跑;重试超限 → failed + 分钟冲正。
    """
    st: JobState = job["state"]
    if st.status is JobStatus.WAITING:
        transition(st, JobStatus.UPLOADING)  # 音频已在云端时该步瞬时通过

    while True:
        try:
            for target, stage in PIPELINE:
                if target is JobStatus.DIARIZING and not job.get("diarize", True):
                    continue  # transcribing → summarizing 由状态机显式允许
                if st.status is not target:
                    transition(st, target)
                stage(job, app)
            transition(st, JobStatus.COMPLETED)
            _notify(job, app, "job_completed")
            return st
        except StageError as e:
            fail_or_retry(st, e.error_code)
            if st.status is JobStatus.FAILED:
                _refund(job, app)
                return st
            transition(st, JobStatus.PREPROCESSING)  # retrying → 重入处理链起点


def publish_outbox(app: AppState) -> int:
    """Outbox Publisher(P0-5):把业务侧原子写入的事件可靠投递到队列。

    生产实现:FOR UPDATE SKIP LOCKED 领取 outbox_events 未投递行 → enqueue
    → 成功后置 published_at;失败记 attempt_count/last_error/next_attempt_at
    退避重试 + 死信告警(至少一次投递,消费侧以 job 状态幂等)。

    先投后删:入队成功才移除事件;失败保留并记录 attempts/last_error。
    """
    published = 0
    while app.outbox:
        event = app.outbox[0]
        try:
            app.queue.enqueue(event["topic"], event["payload"])
        except Exception as e:
            event["attempts"] = event.get("attempts", 0) + 1
            event["last_error"] = str(e)
            break  # 队列不可用:保留事件,本轮终止
        app.outbox.pop(0)
        published += 1
    return published


def run_once(app: AppState) -> int:
    """投递 outbox 并消费一轮队列;返回处理任务数(供测试与 cron 调用)。"""
    publish_outbox(app)
    processed = 0
    while (msg := app.queue.dequeue(TOPIC_TRANSCRIBE)) is not None:
        job = app.jobs.get(msg.payload["job_id"])
        if job is None:
            continue
        process_job(job, app)
        processed += 1
    return processed


def _refund(job: dict, app: AppState) -> None:
    """终态失败:冲正当前 generation 的扣费(幂等,重复调用被 ledger 幂等键拒绝)。

    人工重试会开启新 generation 并重新扣费,此处必须冲正当前代次。
    """
    try:
        app.entitlements.refund_job(job["user_id"], job["id"],
                                    generation=job.get("retry_generation", 0))
    except Exception:
        pass  # DuplicateOperation:已冲正过
    _notify(job, app, "job_failed")


def _notify(job: dict, app: AppState, kind: str) -> None:
    app.notifications.setdefault(job["user_id"], []).append(
        {"type": kind, "job_id": job["id"], "recording_id": job["recording_id"]}
    )
