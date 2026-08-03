"""回访与长期偏好（阶段7）。

- 回访节点：24小时（是否执行）、7天（是否满意）、30天（是否后悔）、
  90天（高价值决策可选）；
- 回访结果更新用户偏好后验，但绝不覆盖原始决策记录；
- 后验只在证据 >= 2 次时影响后续同类决策的初始权重。
"""

from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from decision_engine.personalization.update import (
    Posterior,
    update_posterior,
)

from app.models import DecisionCase, FollowupOutcome, UserPreferencePosterior
from app.models.base import utcnow
from app.services.audit import record_event
from app.services.state_machine import validate_transition
from shared_schemas import DecisionStatus

CHECKPOINTS: dict[str, timedelta] = {
    "h24": timedelta(hours=24),
    "d7": timedelta(days=7),
    "d30": timedelta(days=30),
    "d90": timedelta(days=90),
}


def due_checkpoints(case: DecisionCase, as_of: datetime | None = None) -> list[str]:
    if case.committed_at is None:
        return []
    now = as_of or utcnow()
    committed = case.committed_at
    if committed.tzinfo is None:
        committed = committed.replace(tzinfo=now.tzinfo)
    answered = {f.checkpoint for f in case.followups}
    return [
        cp
        for cp, delta in CHECKPOINTS.items()
        if cp not in answered and now >= committed + delta
    ]


def submit_followup(
    db: Session,
    case: DecisionCase,
    checkpoint: str,
    executed: bool | None,
    satisfaction: float | None,
    regret_level: float | None,
    worried_risk_occurred: bool | None,
    notes: str | None,
) -> FollowupOutcome:
    if checkpoint not in CHECKPOINTS:
        raise ValueError(f"未知回访节点：{checkpoint}")
    status = DecisionStatus(case.status)
    if status not in (DecisionStatus.COMMITTED, DecisionStatus.FOLLOW_UP):
        raise ValueError("只有已锁定的决定才能回访")
    if any(f.checkpoint == checkpoint for f in case.followups):
        raise ValueError(f"回访节点 {checkpoint} 已提交过")

    outcome = FollowupOutcome(
        checkpoint=checkpoint,
        executed=executed,
        satisfaction=satisfaction,
        regret_level=regret_level,
        worried_risk_occurred=worried_risk_occurred,
        notes=notes,
    )
    case.followups.append(outcome)

    if status == DecisionStatus.COMMITTED:
        validate_transition(status, DecisionStatus.FOLLOW_UP)
        case.status = DecisionStatus.FOLLOW_UP
        record_event(
            db,
            case.id,
            "state_transition",
            {"from": status, "to": DecisionStatus.FOLLOW_UP},
            case.user_id,
        )

    db.flush()
    record_event(
        db,
        case.id,
        "followup_recorded",
        {
            "checkpoint": checkpoint,
            "executed": executed,
            "satisfaction": satisfaction,
            "regret_level": regret_level,
        },
        case.user_id,
    )

    # 只有真正执行了决定，本案例的权重才作为偏好证据
    if executed:
        _update_posteriors_from_case(db, case)
    return outcome


def _update_posteriors_from_case(db: Session, case: DecisionCase) -> None:
    for criterion in case.criteria:
        weight = (
            criterion.learned_weight
            if criterion.learned_weight is not None
            else criterion.initial_weight
        )
        row = db.scalar(
            select(UserPreferencePosterior).where(
                UserPreferencePosterior.user_id == case.user_id,
                UserPreferencePosterior.criterion_name == criterion.name,
                UserPreferencePosterior.category == case.domain,
            )
        )
        prior = (
            Posterior(row.posterior_mean, row.posterior_std, row.evidence_count)
            if row
            else None
        )
        updated = update_posterior(prior, weight)
        if row is None:
            db.add(
                UserPreferencePosterior(
                    user_id=case.user_id,
                    criterion_name=criterion.name,
                    category=case.domain,
                    posterior_mean=updated.mean,
                    posterior_std=updated.std,
                    evidence_count=updated.evidence_count,
                )
            )
        else:
            row.posterior_mean = updated.mean
            row.posterior_std = updated.std
            row.evidence_count = updated.evidence_count
    db.flush()
    record_event(
        db,
        case.id,
        "preference_posteriors_updated",
        {"criteria": [c.name for c in case.criteria], "category": case.domain},
        case.user_id,
    )


def list_posteriors(db: Session, user_id: str) -> list[UserPreferencePosterior]:
    return list(
        db.scalars(
            select(UserPreferencePosterior)
            .where(UserPreferencePosterior.user_id == user_id)
            .order_by(UserPreferencePosterior.evidence_count.desc())
        )
    )


def delete_posteriors(db: Session, user_id: str) -> int:
    rows = list_posteriors(db, user_id)
    for row in rows:
        db.delete(row)
    db.flush()
    return len(rows)
