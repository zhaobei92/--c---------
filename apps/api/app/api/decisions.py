import json

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app.db import get_db
from app.gateway import get_model_gateway
from app.models import DecisionCase, HardConstraint, User
from app.services import (
    decision_service,
    intake_service,
    orchestrator,
    preference_service,
)
from app.services.audit import record_event
from model_gateway import ModelGateway
from shared_schemas import (
    AdvanceResponse,
    ComparisonCreateRequest,
    ComparisonState,
    NextComparisonResponse,
    ConstraintCreateRequest,
    ConstraintUpdateRequest,
    DecisionCaseDetail,
    DecisionCaseSummary,
    DecisionCreateRequest,
    DecisionCreateResponse,
    DecisionStatus,
    HardConstraintOut,
    DecisionOptionOut,
    MessageCreateRequest,
    MessageCreateResponse,
    OptionCreateRequest,
    OptionUpdateRequest,
)

router = APIRouter(prefix="/api/v1/decisions", tags=["decisions"])


def get_current_user(db: Session = Depends(get_db)) -> User:
    # 阶段1占位：固定 dev 用户；接入认证后替换为真实身份解析。
    return decision_service.get_or_create_dev_user(db)


def _get_case_or_404(db: Session, decision_id: str, user: User) -> DecisionCase:
    case = decision_service.get_decision(db, decision_id, user)
    if case is None:
        raise HTTPException(status_code=404, detail="decision not found")
    return case


@router.post("", response_model=DecisionCreateResponse, status_code=201)
def create_decision(
    body: DecisionCreateRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> DecisionCreateResponse:
    case = decision_service.create_decision(db, user, body.input)
    return DecisionCreateResponse(
        decision_id=case.id,
        status=DecisionStatus(case.status),
        stream_url=f"/api/v1/decisions/{case.id}/stream",
    )


@router.get("", response_model=list[DecisionCaseSummary])
def list_decisions(
    db: Session = Depends(get_db), user: User = Depends(get_current_user)
) -> list[DecisionCaseSummary]:
    return [
        DecisionCaseSummary.model_validate(c)
        for c in decision_service.list_decisions(db, user)
    ]


@router.get("/{decision_id}", response_model=DecisionCaseDetail)
def get_decision(
    decision_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> DecisionCaseDetail:
    case = _get_case_or_404(db, decision_id, user)
    return DecisionCaseDetail.model_validate(case)


@router.post("/{decision_id}/messages", response_model=MessageCreateResponse, status_code=201)
def create_message(
    decision_id: str,
    body: MessageCreateRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> MessageCreateResponse:
    case = _get_case_or_404(db, decision_id, user)
    user_msg, assistant_msg = decision_service.append_message(db, case, body.content)
    return MessageCreateResponse(
        user_message=user_msg,
        assistant_message=assistant_msg,
        status=DecisionStatus(case.status),
    )


@router.post("/{decision_id}/advance", response_model=AdvanceResponse)
def advance_decision(
    decision_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    gateway: ModelGateway = Depends(get_model_gateway),
) -> AdvanceResponse:
    """Orchestrator 单步推进：诊断 → 偏好比较 → 追问 → 就绪。"""
    case = _get_case_or_404(db, decision_id, user)
    return orchestrator.advance(db, case, gateway)


@router.post("/{decision_id}/analyze", response_model=DecisionCaseDetail)
def analyze_decision(
    decision_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    gateway: ModelGateway = Depends(get_model_gateway),
) -> DecisionCaseDetail:
    """同步执行 Intake 流水线（SSE 之外的非流式入口，幂等）。"""
    case = _get_case_or_404(db, decision_id, user)
    for _ in intake_service.run_intake_pipeline(db, case, gateway):
        pass
    return DecisionCaseDetail.model_validate(case)


@router.get("/{decision_id}/stream")
def stream_decision(
    decision_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    gateway: ModelGateway = Depends(get_model_gateway),
) -> StreamingResponse:
    """SSE：流式执行 Intake 流水线，状态已推进时只回放快照。"""
    case = _get_case_or_404(db, decision_id, user)

    def event_stream():
        for event, data in intake_service.run_intake_pipeline(db, case, gateway):
            yield f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ---------- 成对偏好比较（阶段3） ----------


@router.get("/{decision_id}/comparisons/next", response_model=NextComparisonResponse)
def next_comparison(
    decision_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> NextComparisonResponse:
    case = _get_case_or_404(db, decision_id, user)
    return preference_service.comparison_progress(case)


@router.post("/{decision_id}/comparisons", response_model=ComparisonState, status_code=201)
def create_comparison(
    decision_id: str,
    body: ComparisonCreateRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> ComparisonState:
    case = _get_case_or_404(db, decision_id, user)
    try:
        return preference_service.record_comparison(
            db,
            case,
            left_id=body.left_criterion_id,
            right_id=body.right_criterion_id,
            choice=body.choice,
            strength=body.strength,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


# ---------- 选项编辑（用户可纠正 AI 提取错误，全部落审计日志） ----------


@router.post("/{decision_id}/options", response_model=DecisionOptionOut, status_code=201)
def create_option(
    decision_id: str,
    body: OptionCreateRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> DecisionOptionOut:
    case = _get_case_or_404(db, decision_id, user)
    option = decision_service.add_option(db, case, body.name, body.description)
    record_event(db, case.id, "option_created", {"option": {"id": option.id, "name": option.name}}, user.id)
    return DecisionOptionOut.model_validate(option)


@router.patch("/{decision_id}/options/{option_id}", response_model=DecisionOptionOut)
def update_option(
    decision_id: str,
    option_id: str,
    body: OptionUpdateRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> DecisionOptionOut:
    case = _get_case_or_404(db, decision_id, user)
    option = next((o for o in case.options if o.id == option_id), None)
    if option is None:
        raise HTTPException(status_code=404, detail="option not found")
    before = {"name": option.name, "description": option.description}
    changes = body.model_dump(exclude_unset=True)
    for field, value in changes.items():
        setattr(option, field, value)
    db.flush()
    record_event(db, case.id, "option_updated", {"option_id": option_id, "before": before, "changes": changes}, user.id)
    return DecisionOptionOut.model_validate(option)


@router.delete("/{decision_id}/options/{option_id}", status_code=204)
def delete_option(
    decision_id: str,
    option_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> None:
    case = _get_case_or_404(db, decision_id, user)
    option = next((o for o in case.options if o.id == option_id), None)
    if option is None:
        raise HTTPException(status_code=404, detail="option not found")
    record_event(db, case.id, "option_deleted", {"option_id": option_id, "name": option.name}, user.id)
    db.delete(option)
    db.flush()


# ---------- 约束编辑 ----------


@router.post("/{decision_id}/constraints", response_model=HardConstraintOut, status_code=201)
def create_constraint(
    decision_id: str,
    body: ConstraintCreateRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> HardConstraintOut:
    case = _get_case_or_404(db, decision_id, user)
    constraint = HardConstraint(
        decision_case_id=case.id, description=body.description, is_hard=body.is_hard
    )
    db.add(constraint)
    db.flush()
    record_event(db, case.id, "constraint_created", {"constraint_id": constraint.id, "description": constraint.description}, user.id)
    return HardConstraintOut.model_validate(constraint)


@router.patch("/{decision_id}/constraints/{constraint_id}", response_model=HardConstraintOut)
def update_constraint(
    decision_id: str,
    constraint_id: str,
    body: ConstraintUpdateRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> HardConstraintOut:
    case = _get_case_or_404(db, decision_id, user)
    constraint = next((c for c in case.constraints if c.id == constraint_id), None)
    if constraint is None:
        raise HTTPException(status_code=404, detail="constraint not found")
    before = {"description": constraint.description, "is_hard": constraint.is_hard}
    changes = body.model_dump(exclude_unset=True)
    for field, value in changes.items():
        setattr(constraint, field, value)
    db.flush()
    record_event(db, case.id, "constraint_updated", {"constraint_id": constraint_id, "before": before, "changes": changes}, user.id)
    return HardConstraintOut.model_validate(constraint)


@router.delete("/{decision_id}/constraints/{constraint_id}", status_code=204)
def delete_constraint(
    decision_id: str,
    constraint_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> None:
    case = _get_case_or_404(db, decision_id, user)
    constraint = next((c for c in case.constraints if c.id == constraint_id), None)
    if constraint is None:
        raise HTTPException(status_code=404, detail="constraint not found")
    record_event(db, case.id, "constraint_deleted", {"constraint_id": constraint_id, "description": constraint.description}, user.id)
    db.delete(constraint)
    db.flush()
