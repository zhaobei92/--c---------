"""管理统计（方案阶段8 / 15.3 产品指标）。

阶段8为只读统计；正式上线前需加管理员鉴权。
"""

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import get_db
from app.gateway import get_model_gateway
from app.models import (
    DecisionCase,
    FollowupOutcome,
    ModelInvocation,
    ReopenRequest,
)
from model_gateway import ModelGateway, ModelRole

router = APIRouter(prefix="/api/v1/admin", tags=["admin"])


@router.get("/stats")
def stats(db: Session = Depends(get_db)) -> dict:
    total = db.scalar(select(func.count(DecisionCase.id))) or 0
    by_status = dict(
        db.execute(
            select(DecisionCase.status, func.count(DecisionCase.id)).group_by(
                DecisionCase.status
            )
        ).all()
    )
    committed = sum(
        v for k, v in by_status.items() if k in ("COMMITTED", "FOLLOW_UP")
    )

    reopen_total = db.scalar(select(func.count(ReopenRequest.id))) or 0
    reopen_by_outcome = dict(
        db.execute(
            select(ReopenRequest.outcome, func.count(ReopenRequest.id)).group_by(
                ReopenRequest.outcome
            )
        ).all()
    )
    meaningful = reopen_by_outcome.get("full_reopen", 0)

    invocations = db.scalar(select(func.count(ModelInvocation.id))) or 0
    invocation_success = (
        db.scalar(
            select(func.count(ModelInvocation.id)).where(ModelInvocation.success)
        )
        or 0
    )
    avg_latency = db.scalar(select(func.avg(ModelInvocation.latency_ms)))

    followups = db.scalar(select(func.count(FollowupOutcome.id))) or 0
    executed = (
        db.scalar(
            select(func.count(FollowupOutcome.id)).where(FollowupOutcome.executed)
        )
        or 0
    )
    avg_regret = db.scalar(select(func.avg(FollowupOutcome.regret_level)))

    return {
        "decisions": {
            "total": total,
            "by_status": by_status,
            "completion_rate": round(committed / total, 3) if total else None,
        },
        "reopens": {
            "total": reopen_total,
            "by_outcome": reopen_by_outcome,
            "meaningful_reopen_rate": (
                round(meaningful / reopen_total, 3) if reopen_total else None
            ),
            "rumination_interventions": reopen_by_outcome.get(
                "closure_intervention", 0
            ),
        },
        "followups": {
            "total": followups,
            "action_rate": round(executed / followups, 3) if followups else None,
            "avg_regret": round(avg_regret, 3) if avg_regret is not None else None,
        },
        "model_invocations": {
            "total": invocations,
            "success_rate": (
                round(invocation_success / invocations, 3) if invocations else None
            ),
            "avg_latency_ms": round(avg_latency, 1) if avg_latency else None,
        },
    }


@router.get("/health/models")
def model_health(gateway: ModelGateway = Depends(get_model_gateway)) -> dict:
    """各模型角色的配置状态（不含任何密钥）。"""
    return {
        "credentials_configured": gateway.is_configured,
        "roles": {
            role.value: gateway.role_configured(role) for role in ModelRole
        },
    }
