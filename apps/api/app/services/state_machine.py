"""决策状态机：所有状态转换必须经过 validate_transition。

大模型永远不直接改状态；只有 Orchestrator 依据确定性规则调用这里。
"""

from shared_schemas import DecisionStatus as S


class IllegalTransitionError(Exception):
    def __init__(self, current: S, target: S):
        super().__init__(f"Illegal transition: {current} -> {target}")
        self.current = current
        self.target = target


# 主链路
_MAIN_FLOW: dict[S, set[S]] = {
    S.DRAFT: {S.INTAKE},
    S.INTAKE: {S.RISK_TRIAGE},
    S.RISK_TRIAGE: {S.PROBLEM_NORMALIZATION, S.GUIDED_ONLY},
    S.PROBLEM_NORMALIZATION: {S.STUCK_TYPE_DIAGNOSIS},
    S.STUCK_TYPE_DIAGNOSIS: {S.CONSTRAINT_EXTRACTION},
    S.CONSTRAINT_EXTRACTION: {S.PREFERENCE_ELICITATION},
    S.PREFERENCE_ELICITATION: {S.EVIDENCE_GAP_ANALYSIS},
    S.EVIDENCE_GAP_ANALYSIS: {S.DECISION_COMPUTE, S.EVIDENCE_REQUIRED},
    S.DECISION_COMPUTE: {S.SENSITIVITY_ANALYSIS},
    S.SENSITIVITY_ANALYSIS: {S.CHALLENGE, S.MORE_CLARIFICATION},
    S.CHALLENGE: {S.READY_TO_COMMIT, S.MORE_CLARIFICATION},
    S.READY_TO_COMMIT: {S.COMMITTED, S.MORE_CLARIFICATION},
    S.COMMITTED: {S.FOLLOW_UP},
    S.FOLLOW_UP: set(),
    # 分支状态的回归路径
    S.GUIDED_ONLY: {S.PROBLEM_NORMALIZATION},
    S.EVIDENCE_REQUIRED: {S.EVIDENCE_GAP_ANALYSIS},
    S.MORE_CLARIFICATION: {S.PREFERENCE_ELICITATION, S.EVIDENCE_GAP_ANALYSIS},
    S.CLOSURE_INTERVENTION: {S.FOLLOW_UP, S.EVIDENCE_GAP_ANALYSIS},
}

# 从任意状态可进入的分支状态
_GLOBAL_TARGETS: set[S] = {
    S.GUIDED_ONLY,
    S.EVIDENCE_REQUIRED,
    S.MORE_CLARIFICATION,
    S.CLOSURE_INTERVENTION,
}


def allowed_targets(current: S) -> set[S]:
    return _MAIN_FLOW.get(current, set()) | _GLOBAL_TARGETS


def validate_transition(current: S, target: S) -> None:
    if target not in allowed_targets(current):
        raise IllegalTransitionError(current, target)
