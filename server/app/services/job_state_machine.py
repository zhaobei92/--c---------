"""AI 转写任务状态机。

铁律:transcription_jobs.status 的任何变化必须经过本模块的 ``transition()``,
禁止业务代码直接 UPDATE 状态列。非法迁移抛出 ``IllegalTransition``
(错误码 JOB_4001,属程序缺陷,触发即告警)。

状态图(docs/02-architecture.md §4):

    waiting → uploading → preprocessing → transcribing → diarizing → summarizing → completed
    任一处理态 --失败--> retrying --重入--> 原阶段起点(preprocessing)
    retrying 超限 → failed(终态,已扣分钟冲正)
    completed / failed 为终态,不可再迁移
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class JobStatus(str, Enum):
    WAITING = "waiting"
    UPLOADING = "uploading"
    PREPROCESSING = "preprocessing"
    TRANSCRIBING = "transcribing"
    DIARIZING = "diarizing"
    SUMMARIZING = "summarizing"
    COMPLETED = "completed"
    FAILED = "failed"
    RETRYING = "retrying"


TERMINAL_STATES = frozenset({JobStatus.COMPLETED, JobStatus.FAILED})

#: 处理阶段的正常推进顺序
_PIPELINE_ORDER = [
    JobStatus.WAITING,
    JobStatus.UPLOADING,
    JobStatus.PREPROCESSING,
    JobStatus.TRANSCRIBING,
    JobStatus.DIARIZING,
    JobStatus.SUMMARIZING,
    JobStatus.COMPLETED,
]

#: 允许失败进入 retrying 的状态(waiting 阶段失败无意义,直接重新排队)
_RETRYABLE_FROM = frozenset({
    JobStatus.UPLOADING,
    JobStatus.PREPROCESSING,
    JobStatus.TRANSCRIBING,
    JobStatus.DIARIZING,
    JobStatus.SUMMARIZING,
})

ALLOWED_TRANSITIONS: dict[JobStatus, frozenset[JobStatus]] = {
    **{
        cur: frozenset({_PIPELINE_ORDER[i + 1]})
        for i, cur in enumerate(_PIPELINE_ORDER[:-1])
    },
}
for _s in _RETRYABLE_FROM:
    ALLOWED_TRANSITIONS[_s] = ALLOWED_TRANSITIONS[_s] | {JobStatus.RETRYING, JobStatus.FAILED}
# diarize=false 的任务允许跳过 diarizing
ALLOWED_TRANSITIONS[JobStatus.TRANSCRIBING] = (
    ALLOWED_TRANSITIONS[JobStatus.TRANSCRIBING] | {JobStatus.SUMMARIZING}
)
# retrying 重入处理链起点,或超限失败
ALLOWED_TRANSITIONS[JobStatus.RETRYING] = frozenset({JobStatus.PREPROCESSING, JobStatus.FAILED})
ALLOWED_TRANSITIONS[JobStatus.COMPLETED] = frozenset()
ALLOWED_TRANSITIONS[JobStatus.FAILED] = frozenset()

MAX_RETRIES = 3


class IllegalTransition(Exception):
    """错误码 JOB_4001:状态机拒绝的迁移(程序缺陷,不可自动重试)。"""

    error_code = "JOB_4001"

    def __init__(self, current: JobStatus, target: JobStatus):
        self.current, self.target = current, target
        super().__init__(f"illegal job transition {current.value} -> {target.value}")


class RetryExhausted(Exception):
    """错误码 JOB_4201:重试超限,任务终止(已扣分钟须冲正)。"""

    error_code = "JOB_4201"


@dataclass
class JobState:
    """内存中的任务状态载体;持久化层将其映射到 transcription_jobs 行。"""

    status: JobStatus = JobStatus.WAITING
    retry_count: int = 0
    error_code: str | None = None
    history: list[tuple[str, str]] = field(default_factory=list)  # [(from, to)]


def can_transition(current: JobStatus, target: JobStatus) -> bool:
    return target in ALLOWED_TRANSITIONS[current]


def transition(state: JobState, target: JobStatus, *, error_code: str | None = None) -> JobState:
    """执行一次状态迁移(原地修改并返回 state)。

    - 非法迁移抛 IllegalTransition。
    - 进入 retrying 计数 +1;超过 MAX_RETRIES 时抛 RetryExhausted,
      调用方应改为 transition(state, FAILED)。
    """
    current = state.status
    if not can_transition(current, target):
        raise IllegalTransition(current, target)
    if target is JobStatus.RETRYING:
        if state.retry_count + 1 > MAX_RETRIES:
            raise RetryExhausted(
                f"job exceeded {MAX_RETRIES} retries (last error {error_code or state.error_code})"
            )
        state.retry_count += 1
    state.error_code = error_code if target in (JobStatus.RETRYING, JobStatus.FAILED) else None
    state.history.append((current.value, target.value))
    state.status = target
    return state


def fail_or_retry(state: JobState, error_code: str) -> JobState:
    """处理阶段失败入口:能重试则进 retrying,超限则终态 failed。"""
    try:
        return transition(state, JobStatus.RETRYING, error_code=error_code)
    except RetryExhausted:
        return transition(state, JobStatus.FAILED, error_code="JOB_4201")
