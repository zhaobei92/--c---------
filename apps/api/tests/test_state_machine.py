import pytest

from app.services.state_machine import (
    IllegalTransitionError,
    allowed_targets,
    validate_transition,
)
from shared_schemas import DecisionStatus as S


def test_main_flow_transitions():
    validate_transition(S.DRAFT, S.INTAKE)
    validate_transition(S.INTAKE, S.RISK_TRIAGE)
    validate_transition(S.READY_TO_COMMIT, S.COMMITTED)
    validate_transition(S.COMMITTED, S.FOLLOW_UP)


def test_illegal_transition_rejected():
    with pytest.raises(IllegalTransitionError):
        validate_transition(S.DRAFT, S.COMMITTED)
    with pytest.raises(IllegalTransitionError):
        validate_transition(S.INTAKE, S.DECISION_COMPUTE)


def test_branch_states_reachable_from_anywhere():
    for current in S:
        targets = allowed_targets(current)
        assert S.GUIDED_ONLY in targets
        assert S.CLOSURE_INTERVENTION in targets


def test_committed_cannot_silently_go_back():
    # 已提交的决定不能不经过重开判定直接回到计算阶段
    with pytest.raises(IllegalTransitionError):
        validate_transition(S.COMMITTED, S.DECISION_COMPUTE)
