"""偏好学习（阶段3）：标准生成 → 成对比较 → Bradley-Terry 权重。

- 标准由 LLM 生成（criteria_generator prompt），失败降级为领域默认标准；
- 权重只来自用户的成对比较，LLM 生成的标准不携带权重；
- 每次比较后立即重新拟合；冲突回答抬高不确定度，不强行给高置信结果。
"""

import time
from itertools import combinations

from sqlalchemy import select
from sqlalchemy.orm import Session

from decision_engine.personalization.update import Posterior, blend_with_default
from decision_engine.preference.bradley_terry import Comparison, fit_preferences

from app.models import DecisionCase, DecisionCriterion, ModelInvocation, PairwiseComparison
from app.services.audit import record_event
from app.services.intake_service import load_prompt
from model_gateway import ModelGateway, ModelRole
from model_gateway.client import ModelCallError
from model_gateway.gateway import ModelNotConfiguredError
from shared_schemas import (
    AdvanceResponse,
    ComparisonState,
    CriteriaGeneration,
    CriterionOut,
    DecisionDomain,
    NextComparisonResponse,
)

# 领域默认标准（LLM 不可用时的确定性兜底）：(名称, 效用曲线)
_DOMAIN_DEFAULT_CRITERIA: dict[str, list[tuple[str, str]]] = {
    DecisionDomain.PRODUCT: [
        ("价格", "loss_averse"),
        ("核心性能", "diminishing"),
        ("日常舒适度", "linear"),
        ("售后保修", "threshold"),
    ],
    DecisionDomain.WORK_PRIORITY: [
        ("紧急程度", "s_curve"),
        ("长期重要性", "linear"),
        ("精力匹配", "linear"),
        ("预期收益", "diminishing"),
    ],
    DecisionDomain.LEARNING: [
        ("内容质量", "diminishing"),
        ("时间成本", "loss_averse"),
        ("费用", "loss_averse"),
        ("兴趣匹配", "linear"),
    ],
    DecisionDomain.CAREER: [
        ("成长空间", "diminishing"),
        ("回报", "linear"),
        ("稳定性", "threshold"),
        ("兴趣匹配", "linear"),
    ],
    DecisionDomain.PLAN: [
        ("体验质量", "linear"),
        ("总成本", "loss_averse"),
        ("便利程度", "linear"),
        ("风险", "threshold"),
    ],
}
_FALLBACK_CRITERIA: list[tuple[str, str]] = [
    ("长期满意度", "linear"),
    ("成本代价", "loss_averse"),
    ("风险", "threshold"),
]


def _default_criteria(case: DecisionCase) -> list[tuple[str, str]]:
    base = list(_DOMAIN_DEFAULT_CRITERIA.get(case.domain, _FALLBACK_CRITERIA))
    names = {n for n, _ in base}
    for concern in list(case.concerns)[:2]:
        name = concern[:20]
        if name not in names:
            base.append((name, "linear"))
            names.add(name)
    return base[:6]


def _case_snapshot_text(case: DecisionCase) -> str:
    return (
        f"标题：{case.title}\n领域：{case.domain}\n"
        f"选项：{'、'.join(o.name for o in case.options)}\n"
        f"事实：{'；'.join(case.facts) or '无'}\n"
        f"约束：{'；'.join(c.description for c in case.constraints) or '无'}\n"
        f"担忧：{'；'.join(case.concerns) or '无'}"
    )


def ensure_criteria(db: Session, case: DecisionCase, gateway: ModelGateway) -> None:
    """标准不存在时生成（LLM 优先，降级为领域默认）。幂等。"""
    if case.criteria:
        return
    start = time.monotonic()
    try:
        generated = gateway.structured(
            ModelRole.FAST,
            system_prompt=load_prompt("diagnosis/criteria_generator.md"),
            user_content=_case_snapshot_text(case),
            schema=CriteriaGeneration,
        )
        pairs = [(c.name[:60], c.utility_curve_type) for c in generated.criteria[:6]]
        source, success, error = "ai_generated", True, None
        if len(pairs) < 2:
            pairs, source = _default_criteria(case), "domain_default"
    except (ModelNotConfiguredError, ModelCallError) as exc:
        pairs, source = _default_criteria(case), "domain_default"
        success, error = False, str(exc)[:2000]
    db.add(
        ModelInvocation(
            decision_case_id=case.id,
            task_kind="criteria_generation",
            model_role=ModelRole.FAST,
            model_name=gateway.settings.MODEL_FAST,
            success=success,
            error=error,
            latency_ms=(time.monotonic() - start) * 1000,
        )
    )

    default_initial = round(1.0 / len(pairs), 4)
    applied_posteriors: list[str] = []
    for name, curve in pairs:
        initial = default_initial
        posterior_row = _find_posterior(db, case.user_id, name, case.domain)
        if posterior_row is not None:
            blended = blend_with_default(
                Posterior(
                    posterior_row.posterior_mean,
                    posterior_row.posterior_std,
                    posterior_row.evidence_count,
                ),
                default_initial,
            )
            if blended != default_initial:
                initial = blended
                applied_posteriors.append(name)
        case.criteria.append(
            DecisionCriterion(
                name=name,
                utility_curve_type=curve,
                initial_weight=initial,
                source=source,
            )
        )
    db.flush()
    record_event(
        db,
        case.id,
        "criteria_generated",
        {
            "source": source,
            "criteria": [n for n, _ in pairs],
            "posteriors_applied": applied_posteriors,
        },
        case.user_id,
    )


def _find_posterior(db: Session, user_id: str, name: str, category: str):
    from app.models import UserPreferencePosterior

    return db.scalar(
        select(UserPreferencePosterior).where(
            UserPreferencePosterior.user_id == user_id,
            UserPreferencePosterior.criterion_name == name,
            UserPreferencePosterior.category == category,
        )
    )


def required_comparisons(case: DecisionCase) -> int:
    n = len(case.criteria)
    if n < 2:
        return 0
    all_pairs = n * (n - 1) // 2
    return min(all_pairs, max(3, n - 1))


def _valid_comparisons(case: DecisionCase) -> list[PairwiseComparison]:
    ids = {c.id for c in case.criteria}
    return [
        c
        for c in case.comparisons
        if c.left_criterion_id in ids and c.right_criterion_id in ids
    ]


def next_pair(case: DecisionCase) -> tuple[DecisionCriterion, DecisionCriterion] | None:
    """选被比较次数最少的一对标准；全部对都问过一轮后返回 None。"""
    criteria = list(case.criteria)
    if len(criteria) < 2:
        return None
    counts: dict[tuple[str, str], int] = {}
    for a, b in combinations(criteria, 2):
        counts[(a.id, b.id)] = 0
    for comp in _valid_comparisons(case):
        key = tuple(sorted((comp.left_criterion_id, comp.right_criterion_id)))
        ordered = next(
            (k for k in counts if tuple(sorted(k)) == key), None
        )
        if ordered:
            counts[ordered] += 1
    unasked = [pair for pair, n in counts.items() if n == 0]
    if not unasked:
        return None
    by_id = {c.id: c for c in criteria}
    a_id, b_id = unasked[0]
    return by_id[a_id], by_id[b_id]


def fit_and_store(db: Session, case: DecisionCase) -> ComparisonState:
    """用全部有效比较拟合 BT 权重并写回标准。"""
    criteria = list(case.criteria)
    index_of = {c.id: i for i, c in enumerate(criteria)}
    comparisons = [
        Comparison(
            left=index_of[c.left_criterion_id],
            right=index_of[c.right_criterion_id],
            choice=c.choice,
            strength=c.strength,
        )
        for c in _valid_comparisons(case)
    ]
    fit = fit_preferences(len(criteria), comparisons)
    for criterion, weight, unc in zip(criteria, fit.weights, fit.uncertainty):
        criterion.learned_weight = round(weight, 4)
        criterion.weight_uncertainty = round(unc, 4)
    db.flush()
    record_event(
        db,
        case.id,
        "preference_weights_updated",
        {
            "weights": {c.name: c.learned_weight for c in criteria},
            "consistency": round(fit.consistency, 3),
            "conflict_pairs": fit.conflict_pairs,
            "comparisons_used": fit.comparisons_used,
        },
        case.user_id,
    )
    return ComparisonState(
        comparisons_done=len(_valid_comparisons(case)),
        comparisons_required=required_comparisons(case),
        consistency=round(fit.consistency, 3),
        criteria=[CriterionOut.model_validate(c) for c in criteria],
    )


def record_comparison(
    db: Session,
    case: DecisionCase,
    left_id: str,
    right_id: str,
    choice: str,
    strength: float,
) -> ComparisonState:
    ids = {c.id for c in case.criteria}
    if left_id not in ids or right_id not in ids or left_id == right_id:
        raise ValueError("invalid criterion pair")
    case.comparisons.append(
        PairwiseComparison(
            left_criterion_id=left_id,
            right_criterion_id=right_id,
            choice=choice,
            strength=strength,
        )
    )
    db.flush()
    return fit_and_store(db, case)


def comparison_progress(case: DecisionCase) -> NextComparisonResponse:
    done = len(_valid_comparisons(case))
    required = required_comparisons(case)
    pair = next_pair(case) if done < required else None
    if pair is None:
        return NextComparisonResponse(
            done=True, comparisons_done=done, comparisons_required=required
        )
    left, right = pair
    return NextComparisonResponse(
        done=False,
        left=CriterionOut.model_validate(left),
        right=CriterionOut.model_validate(right),
        prompt=f"「{left.name}」和「{right.name}」，哪个对这个决定更重要？",
        comparisons_done=done,
        comparisons_required=required,
    )


def preference_step(
    db: Session, case: DecisionCase, gateway: ModelGateway
) -> AdvanceResponse | None:
    """Orchestrator 接缝：需要比较时返回 AdvanceResponse，偏好就绪时返回 None。"""
    ensure_criteria(db, case, gateway)
    done = len(_valid_comparisons(case))
    required = required_comparisons(case)
    if done < required and next_pair(case) is not None:
        return AdvanceResponse(
            kind="comparison_needed",
            status=case.status,
            message=f"请完成偏好比较（{done}/{required}）。",
        )
    fit_and_store(db, case)
    return None
