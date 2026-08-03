"""选项×标准评估（阶段4）。

以期望值+不确定度保存；缺失的评估在引擎中按中性 0.5、高不确定 0.3 处理，
所以评估不是计算的硬前提，但 Orchestrator 会先提示补齐。
"""

from sqlalchemy.orm import Session

from app.models import DecisionCase, OptionEvaluation
from app.services.audit import record_event


def evaluations_complete(case: DecisionCase) -> bool:
    """每个（合格选项 × 标准）都有评估才算完整。无标准时视为完整。"""
    if not case.criteria:
        return True
    eligible_options = [o for o in case.options if o.is_eligible]
    if not eligible_options:
        return True
    existing = {(e.option_id, e.criterion_id) for e in case.evaluations}
    for option in eligible_options:
        for criterion in case.criteria:
            if (option.id, criterion.id) not in existing:
                return False
    return True


def upsert_evaluations(
    db: Session, case: DecisionCase, items: list[dict]
) -> list[OptionEvaluation]:
    """批量写入评估；同一 (option, criterion) 覆盖更新。"""
    option_ids = {o.id for o in case.options}
    criterion_ids = {c.id for c in case.criteria}
    existing = {(e.option_id, e.criterion_id): e for e in case.evaluations}
    touched: list[OptionEvaluation] = []

    for item in items:
        option_id, criterion_id = item["option_id"], item["criterion_id"]
        if option_id not in option_ids or criterion_id not in criterion_ids:
            raise ValueError(f"unknown option/criterion pair: {option_id}/{criterion_id}")
        value = min(1.0, max(0.0, float(item["expected_value"])))
        uncertainty = min(0.5, max(0.01, float(item.get("uncertainty", 0.15))))
        key = (option_id, criterion_id)
        if key in existing:
            ev = existing[key]
            ev.expected_value = value
            ev.uncertainty = uncertainty
        else:
            ev = OptionEvaluation(
                option_id=option_id,
                criterion_id=criterion_id,
                expected_value=value,
                uncertainty=uncertainty,
            )
            case.evaluations.append(ev)
            existing[key] = ev
        touched.append(ev)

    db.flush()
    record_event(
        db,
        case.id,
        "evaluations_updated",
        {"count": len(touched)},
        case.user_id,
    )
    return touched
