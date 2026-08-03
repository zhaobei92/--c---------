"""Decision Orchestrator：按状态机推进决策流程，一次一步。

大模型永远不决定流程走向；这里的每个转换都经过 validate_transition
并写入审计日志。/advance 是幂等的：同一状态重复调用不会重复产生副作用
（追问除外——每次调用最多发出一个新问题）。
"""

from sqlalchemy.orm import Session

from app.models import DecisionCase, DecisionMessage
from app.services import diagnosis_service, question_service
from app.services.audit import record_event
from app.services.state_machine import validate_transition
from model_gateway import ModelGateway
from shared_schemas import (
    AdvanceResponse,
    DecisionStatus,
    MessageRole,
    StuckTypeDiagnosis,
)


def _transition(db: Session, case: DecisionCase, target: DecisionStatus) -> None:
    current = DecisionStatus(case.status)
    validate_transition(current, target)
    case.status = target
    record_event(
        db, case.id, "state_transition", {"from": current, "to": target}, case.user_id
    )
    db.flush()


def _say(db: Session, case: DecisionCase, content: str) -> None:
    case.messages.append(DecisionMessage(role=MessageRole.ASSISTANT, content=content))
    db.flush()


def _diagnosis_message(diagnosis: StuckTypeDiagnosis) -> str:
    labels = {
        "information_gap": "信息还不够",
        "value_conflict": "几个在意的点在打架",
        "regret_aversion": "怕选错后悔",
        "uncertainty_distress": "不确定感本身让人难受",
        "rumination": "在没有新信息的情况下反复想",
        "social_pressure": "他人的看法带来压力",
        "identity_conflict": "选择和自我认同有关",
        "action_avoidance": "行动本身让人想拖延",
        "sunk_cost": "已投入的成本在牵制",
        "perfectionism": "想找到绝对最优解",
    }
    primary = labels.get(diagnosis.primary_type, diagnosis.primary_type)
    parts = [f"听下来，你现在卡住的主要原因更像是：{primary}。"]
    if diagnosis.explanation_summary:
        parts.append(diagnosis.explanation_summary)
    parts.append("接下来我会先了解你的真实偏好，然后一起把这个决定定下来。")
    return "".join(parts)


def advance(db: Session, case: DecisionCase, gateway: ModelGateway) -> AdvanceResponse:
    status = DecisionStatus(case.status)

    if status in (DecisionStatus.DRAFT, DecisionStatus.INTAKE, DecisionStatus.RISK_TRIAGE):
        return AdvanceResponse(
            kind="noop",
            status=case.status,
            message="请先完成录入解析（调用 /analyze 或连接 /stream）。",
        )

    if status == DecisionStatus.GUIDED_ONLY:
        return AdvanceResponse(
            kind="guided_only",
            status=case.status,
            message="该决策为高影响或受限类别，系统只提供梳理协助，不直接给出结论。",
        )

    if status == DecisionStatus.PROBLEM_NORMALIZATION:
        diagnosis, source = diagnosis_service.diagnose(db, case, gateway)
        case.primary_stuck_type = diagnosis.primary_type
        case.stuck_type_scores = {k: v for k, v in diagnosis.type_scores.items()}
        case.information_completeness = diagnosis.information_completeness
        case.decision_readiness = diagnosis.decision_readiness
        record_event(
            db,
            case.id,
            "stuck_diagnosis",
            {"source": source, "diagnosis": diagnosis.model_dump()},
            case.user_id,
        )
        _transition(db, case, DecisionStatus.STUCK_TYPE_DIAGNOSIS)
        _transition(db, case, DecisionStatus.CONSTRAINT_EXTRACTION)
        _transition(db, case, DecisionStatus.PREFERENCE_ELICITATION)
        _say(db, case, _diagnosis_message(diagnosis))
        return AdvanceResponse(
            kind="diagnosis", status=case.status, diagnosis=diagnosis
        )

    if status == DecisionStatus.PREFERENCE_ELICITATION:
        # 阶段3在此接入成对比较；标准或比较尚未就绪时直接进入信息缺口分析
        from app.services import preference_service

        step = preference_service.preference_step(db, case, gateway)
        if step is not None:
            return step
        _transition(db, case, DecisionStatus.EVIDENCE_GAP_ANALYSIS)
        return advance(db, case, gateway)

    if status == DecisionStatus.EVIDENCE_GAP_ANALYSIS:
        question = question_service.next_question(case)
        if question is not None:
            question_service.record_asked(case, question)
            record_event(
                db, case.id, "question_asked", question.model_dump(), case.user_id
            )
            _say(db, case, question.question)
            return AdvanceResponse(
                kind="question", status=case.status, question=question
            )

        from app.services import evaluation_service

        if not evaluation_service.evaluations_complete(case):
            return AdvanceResponse(
                kind="evaluations_needed",
                status=case.status,
                message="请为每个选项在各标准上打分（也可以先跳过用默认中性值）。",
            )
        case.decision_readiness = diagnosis_service.compute_decision_readiness(
            case,
            comparisons_done=len(case.comparisons) if hasattr(case, "comparisons") else 0,
            evaluation_coverage=1.0,
        )
        db.flush()
        return AdvanceResponse(
            kind="ready_to_compute",
            status=case.status,
            message="信息已足够，可以运行决策分析了。",
        )

    if status in (
        DecisionStatus.DECISION_COMPUTE,
        DecisionStatus.SENSITIVITY_ANALYSIS,
        DecisionStatus.CHALLENGE,
        DecisionStatus.READY_TO_COMMIT,
    ):
        return AdvanceResponse(
            kind="recommendation_ready",
            status=case.status,
            message="分析已完成，请查看结果页。",
        )

    return AdvanceResponse(kind="noop", status=case.status)
