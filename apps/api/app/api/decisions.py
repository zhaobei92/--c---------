from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import User
from app.services import decision_service
from shared_schemas import (
    DecisionCaseDetail,
    DecisionCaseSummary,
    DecisionCreateRequest,
    DecisionCreateResponse,
    DecisionStatus,
    MessageCreateRequest,
    MessageCreateResponse,
)

router = APIRouter(prefix="/api/v1/decisions", tags=["decisions"])


def get_current_user(db: Session = Depends(get_db)) -> User:
    # 阶段1占位：固定 dev 用户；接入认证后替换为真实身份解析。
    return decision_service.get_or_create_dev_user(db)


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
    case = decision_service.get_decision(db, decision_id, user)
    if case is None:
        raise HTTPException(status_code=404, detail="decision not found")
    return DecisionCaseDetail.model_validate(case)


@router.post("/{decision_id}/messages", response_model=MessageCreateResponse, status_code=201)
def create_message(
    decision_id: str,
    body: MessageCreateRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> MessageCreateResponse:
    case = decision_service.get_decision(db, decision_id, user)
    if case is None:
        raise HTTPException(status_code=404, detail="decision not found")
    user_msg, assistant_msg = decision_service.append_message(db, case, body.content)
    return MessageCreateResponse(
        user_message=user_msg,
        assistant_message=assistant_msg,
        status=DecisionStatus(case.status),
    )
