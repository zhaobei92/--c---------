from enum import StrEnum


class DecisionStatus(StrEnum):
    """决策状态机的全部状态。

    阶段1只会用到 DRAFT / INTAKE，其余状态先占位，
    转换合法性由 apps/api/app/services/state_machine.py 控制。
    """

    DRAFT = "DRAFT"
    INTAKE = "INTAKE"
    RISK_TRIAGE = "RISK_TRIAGE"
    PROBLEM_NORMALIZATION = "PROBLEM_NORMALIZATION"
    STUCK_TYPE_DIAGNOSIS = "STUCK_TYPE_DIAGNOSIS"
    CONSTRAINT_EXTRACTION = "CONSTRAINT_EXTRACTION"
    PREFERENCE_ELICITATION = "PREFERENCE_ELICITATION"
    EVIDENCE_GAP_ANALYSIS = "EVIDENCE_GAP_ANALYSIS"
    DECISION_COMPUTE = "DECISION_COMPUTE"
    SENSITIVITY_ANALYSIS = "SENSITIVITY_ANALYSIS"
    CHALLENGE = "CHALLENGE"
    READY_TO_COMMIT = "READY_TO_COMMIT"
    COMMITTED = "COMMITTED"
    FOLLOW_UP = "FOLLOW_UP"
    # 分支状态
    GUIDED_ONLY = "GUIDED_ONLY"
    EVIDENCE_REQUIRED = "EVIDENCE_REQUIRED"
    MORE_CLARIFICATION = "MORE_CLARIFICATION"
    CLOSURE_INTERVENTION = "CLOSURE_INTERVENTION"


class RiskLevel(StrEnum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    RESTRICTED = "RESTRICTED"


class DecisionDomain(StrEnum):
    PRODUCT = "product"
    PLAN = "plan"
    WORK_PRIORITY = "work_priority"
    LEARNING = "learning"
    CAREER = "career"
    RELATIONSHIP = "relationship"
    RESTRICTED = "restricted"
    OTHER = "other"


class StuckType(StrEnum):
    INFORMATION_GAP = "information_gap"
    VALUE_CONFLICT = "value_conflict"
    REGRET_AVERSION = "regret_aversion"
    UNCERTAINTY_DISTRESS = "uncertainty_distress"
    RUMINATION = "rumination"
    SOCIAL_PRESSURE = "social_pressure"
    IDENTITY_CONFLICT = "identity_conflict"
    ACTION_AVOIDANCE = "action_avoidance"
    SUNK_COST = "sunk_cost"
    PERFECTIONISM = "perfectionism"


class MessageRole(StrEnum):
    USER = "user"
    ASSISTANT = "assistant"
    SYSTEM = "system"


class InputType(StrEnum):
    TEXT = "text"
