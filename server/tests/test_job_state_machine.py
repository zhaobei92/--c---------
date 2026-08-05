import pytest

from app.services.job_state_machine import (
    ALLOWED_TRANSITIONS, MAX_RETRIES, IllegalTransition, JobState, JobStatus,
    fail_or_retry, transition,
)


def _advance(state, *targets):
    for t in targets:
        transition(state, t)
    return state


HAPPY_PATH = [
    JobStatus.UPLOADING, JobStatus.PREPROCESSING, JobStatus.TRANSCRIBING,
    JobStatus.DIARIZING, JobStatus.SUMMARIZING, JobStatus.COMPLETED,
]


def test_happy_path_full_pipeline():
    st = _advance(JobState(), *HAPPY_PATH)
    assert st.status is JobStatus.COMPLETED
    assert st.retry_count == 0
    assert [t for _, t in st.history][-1] == "completed"


def test_skip_diarize_path():
    st = _advance(JobState(), JobStatus.UPLOADING, JobStatus.PREPROCESSING,
                  JobStatus.TRANSCRIBING, JobStatus.SUMMARIZING, JobStatus.COMPLETED)
    assert st.status is JobStatus.COMPLETED


@pytest.mark.parametrize("target", [s for s in JobStatus if s is not JobStatus.UPLOADING])
def test_waiting_only_allows_uploading(target):
    st = JobState()
    with pytest.raises(IllegalTransition):
        transition(st, target)


def test_terminal_states_locked():
    done = _advance(JobState(), *HAPPY_PATH)
    failed = fail_or_retry(_advance(JobState(), JobStatus.UPLOADING), "JOB_4101")
    for _ in range(MAX_RETRIES):
        if failed.status is JobStatus.RETRYING:
            transition(failed, JobStatus.PREPROCESSING)
            fail_or_retry(failed, "JOB_4101")
    assert failed.status is JobStatus.FAILED
    for terminal in (done, failed):
        for target in JobStatus:
            with pytest.raises(IllegalTransition):
                transition(terminal, target)


def test_retry_counts_and_exhaustion():
    st = _advance(JobState(), JobStatus.UPLOADING, JobStatus.PREPROCESSING)
    for i in range(MAX_RETRIES):
        fail_or_retry(st, "JOB_4102")
        assert st.status is JobStatus.RETRYING
        assert st.retry_count == i + 1
        transition(st, JobStatus.PREPROCESSING)
    fail_or_retry(st, "JOB_4102")
    assert st.status is JobStatus.FAILED
    assert st.error_code == "JOB_4201"


def test_error_code_cleared_on_success_transition():
    st = _advance(JobState(), JobStatus.UPLOADING)
    fail_or_retry(st, "JOB_4101")
    assert st.error_code == "JOB_4101"
    transition(st, JobStatus.PREPROCESSING)
    assert st.error_code is None


def test_transition_table_is_closed_over_enum():
    for source, targets in ALLOWED_TRANSITIONS.items():
        assert isinstance(source, JobStatus)
        for t in targets:
            assert isinstance(t, JobStatus)
    # 每个非终态都必须有出边
    for s in JobStatus:
        if s in (JobStatus.COMPLETED, JobStatus.FAILED):
            assert not ALLOWED_TRANSITIONS[s]
        else:
            assert ALLOWED_TRANSITIONS[s]
