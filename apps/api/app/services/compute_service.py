"""决策计算（阶段4）：把数据库状态映射为引擎输入，运行确定性分析。

LLM 不参与任何数值计算。每次运行都版本化保存输入/输出快照，
固定 seed 保证可复现。
"""

from sqlalchemy.orm import Session

from decision_engine.engine import ALGORITHM_VERSION, run_analysis
from decision_engine.types import CriterionSpec, EvaluationSpec, OptionSpec

from app.models import DecisionCase, DecisionRun
from app.services.audit import record_event
from app.services.state_machine import validate_transition
from shared_schemas import DecisionStatus

DEFAULT_SEED = 42
DEFAULT_WEIGHT_UNCERTAINTY = 0.15


def _build_specs(
    case: DecisionCase,
) -> tuple[list[OptionSpec], list[CriterionSpec], list[EvaluationSpec]]:
    options = [
        OptionSpec(
            key=o.id,
            name=o.name,
            eligible=o.is_eligible,
            ineligible_reason=o.elimination_reason,
        )
        for o in case.options
    ]
    criteria = [
        CriterionSpec(
            key=c.id,
            name=c.name,
            weight=c.learned_weight if c.learned_weight is not None else c.initial_weight,
            weight_uncertainty=(
                c.weight_uncertainty
                if c.weight_uncertainty is not None
                else DEFAULT_WEIGHT_UNCERTAINTY
            ),
            curve_type=c.utility_curve_type,
            direction=c.direction,
            criterion_type=c.criterion_type,
            veto_threshold=c.veto_threshold,
            minimum_acceptable=c.minimum_acceptable,
        )
        for c in case.criteria
    ]
    evaluations = [
        EvaluationSpec(
            option_key=e.option_id,
            criterion_key=e.criterion_id,
            expected_value=e.expected_value,
            uncertainty=e.uncertainty,
        )
        for e in case.evaluations
    ]
    return options, criteria, evaluations


def _input_snapshot(case: DecisionCase) -> dict:
    return {
        "options": [
            {"id": o.id, "name": o.name, "is_eligible": o.is_eligible}
            for o in case.options
        ],
        "criteria": [
            {
                "id": c.id,
                "name": c.name,
                "weight": c.learned_weight if c.learned_weight is not None else c.initial_weight,
                "weight_uncertainty": c.weight_uncertainty,
                "curve": c.utility_curve_type,
                "type": c.criterion_type,
            }
            for c in case.criteria
        ],
        "evaluations": [
            {
                "option_id": e.option_id,
                "criterion_id": e.criterion_id,
                "expected_value": e.expected_value,
                "uncertainty": e.uncertainty,
            }
            for e in case.evaluations
        ],
        "constraints": [
            {"description": c.description, "is_hard": c.is_hard}
            for c in case.constraints
        ],
    }


def _transition(db: Session, case: DecisionCase, target: DecisionStatus) -> None:
    current = DecisionStatus(case.status)
    validate_transition(current, target)
    case.status = target
    record_event(
        db, case.id, "state_transition", {"from": current, "to": target}, case.user_id
    )
    db.flush()


def run_compute(
    db: Session, case: DecisionCase, seed: int = DEFAULT_SEED
) -> DecisionRun:
    """运行引擎并保存 DecisionRun。要求状态在 EVIDENCE_GAP_ANALYSIS 及之后。"""
    if len([o for o in case.options if o.is_eligible]) < 2:
        raise ValueError("至少需要两个合格选项才能计算")
    if not case.criteria:
        raise ValueError("尚未生成决策标准")

    status = DecisionStatus(case.status)
    if status == DecisionStatus.EVIDENCE_GAP_ANALYSIS:
        _transition(db, case, DecisionStatus.DECISION_COMPUTE)
    elif status not in (
        DecisionStatus.DECISION_COMPUTE,
        DecisionStatus.SENSITIVITY_ANALYSIS,
        DecisionStatus.CHALLENGE,
        DecisionStatus.READY_TO_COMMIT,
    ):
        raise ValueError(f"当前状态 {status} 不允许运行计算")

    options, criteria, evaluations = _build_specs(case)
    result = run_analysis(options, criteria, evaluations, seed=seed)

    run = DecisionRun(
        algorithm_version=ALGORITHM_VERSION,
        seed=seed,
        input_snapshot=_input_snapshot(case),
        result_snapshot=result.to_dict(),
        ranking_stability=result.ranking_stability,
        winning_option_id=result.winner,
    )
    case.runs.append(run)
    db.flush()
    record_event(
        db,
        case.id,
        "decision_computed",
        {
            "run_id": run.id,
            "algorithm_version": ALGORITHM_VERSION,
            "seed": seed,
            "winner": result.winner,
            "ranking_stability": result.ranking_stability,
        },
        case.user_id,
    )

    if DecisionStatus(case.status) == DecisionStatus.DECISION_COMPUTE:
        _transition(db, case, DecisionStatus.SENSITIVITY_ANALYSIS)
    return run


def latest_run(case: DecisionCase) -> DecisionRun | None:
    return case.runs[-1] if case.runs else None
