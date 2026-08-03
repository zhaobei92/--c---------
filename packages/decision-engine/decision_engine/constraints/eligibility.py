"""硬约束与非补偿性规则过滤。

Veto：任何 veto 标准低于底线的方案直接淘汰，任何补偿性优势都救不回来。
Aspiration（threshold 类型 + minimum_acceptable）：不淘汰但必须显式警告。
"""

from decision_engine.types import (
    CriterionSpec,
    EliminationRecord,
    EvaluationSpec,
    OptionSpec,
)


def _effective_value(criterion: CriterionSpec, raw: float) -> float:
    return 1.0 - raw if criterion.direction == "lower_better" else raw


def filter_eligible(
    options: list[OptionSpec],
    criteria: list[CriterionSpec],
    evaluations: list[EvaluationSpec],
) -> tuple[list[OptionSpec], list[EliminationRecord], list[str]]:
    """返回 (合格选项, 淘汰记录, aspiration 警告)。"""
    eval_map = {(e.option_key, e.criterion_key): e for e in evaluations}
    eliminated: list[EliminationRecord] = []
    warnings: list[str] = []
    eligible: list[OptionSpec] = []

    for option in options:
        if not option.eligible:
            eliminated.append(
                EliminationRecord(
                    option.key, option.ineligible_reason or "被用户或前置规则排除"
                )
            )
            continue

        veto_reason = None
        for criterion in criteria:
            ev = eval_map.get((option.key, criterion.key))
            if ev is None:
                continue
            value = _effective_value(criterion, ev.expected_value)
            if (
                criterion.criterion_type == "veto"
                and criterion.veto_threshold is not None
                and value < criterion.veto_threshold
            ):
                veto_reason = (
                    f"「{criterion.name}」低于不可妥协底线"
                    f"（{value:.2f} < {criterion.veto_threshold:.2f}）"
                )
                break
            if (
                criterion.criterion_type == "threshold"
                and criterion.minimum_acceptable is not None
                and value < criterion.minimum_acceptable
            ):
                warnings.append(
                    f"{option.name} 在「{criterion.name}」上低于期望水平"
                    f"（{value:.2f} < {criterion.minimum_acceptable:.2f}）"
                )

        if veto_reason:
            eliminated.append(EliminationRecord(option.key, veto_reason))
        else:
            eligible.append(option)

    return eligible, eliminated, warnings
