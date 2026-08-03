"""确定性敏感性分析：找出会翻转推荐的具体条件。"""

from dataclasses import replace

from decision_engine.monte_carlo.simulate import deterministic_utilities
from decision_engine.types import (
    CriterionSpec,
    EvaluationSpec,
    OptionSpec,
    SensitivityFlip,
)

_WEIGHT_SCALES = [(0.5, "权重降低50%"), (1.5, "权重提高50%")]
_SCORE_SHIFTS = [(-0.15, "表现下调0.15"), (0.15, "表现上调0.15")]


def _winner(options, criteria, evaluations) -> str | None:
    utils = deterministic_utilities(options, criteria, evaluations)
    if not utils:
        return None
    return max(utils, key=utils.get)


def find_flips(
    options: list[OptionSpec],
    criteria: list[CriterionSpec],
    evaluations: list[EvaluationSpec],
) -> list[SensitivityFlip]:
    """逐一扰动权重与关键表现值，记录使赢家改变的条件。"""
    flips: list[SensitivityFlip] = []
    base_winner = _winner(options, criteria, evaluations)
    if base_winner is None or len(options) < 2:
        return flips
    name_of = {o.key: o.name for o in options}

    # 权重扰动
    for idx, criterion in enumerate(criteria):
        for scale, label in _WEIGHT_SCALES:
            perturbed = list(criteria)
            perturbed[idx] = replace(criterion, weight=criterion.weight * scale)
            new_winner = _winner(options, perturbed, evaluations)
            if new_winner and new_winner != base_winner:
                flips.append(
                    SensitivityFlip(
                        variable=f"weight:{criterion.name}",
                        change=label,
                        new_winner=name_of.get(new_winner, new_winner),
                    )
                )

    # 表现扰动：只扰动赢家与第二名的评估（其余选项翻不动结论）
    utils = deterministic_utilities(options, criteria, evaluations)
    top2_keys = [k for k, _ in sorted(utils.items(), key=lambda kv: -kv[1])[:2]]
    for i, ev in enumerate(evaluations):
        if ev.option_key not in top2_keys:
            continue
        for shift, label in _SCORE_SHIFTS:
            new_value = min(1.0, max(0.0, ev.expected_value + shift))
            if new_value == ev.expected_value:
                continue
            perturbed_evals = list(evaluations)
            perturbed_evals[i] = replace(ev, expected_value=new_value)
            new_winner = _winner(options, criteria, perturbed_evals)
            if new_winner and new_winner != base_winner:
                crit_name = next(
                    (c.name for c in criteria if c.key == ev.criterion_key),
                    ev.criterion_key,
                )
                flips.append(
                    SensitivityFlip(
                        variable=f"score:{name_of.get(ev.option_key, ev.option_key)}:{crit_name}",
                        change=label,
                        new_winner=name_of.get(new_winner, new_winner),
                    )
                )

    # 去重（同变量同方向只保留一条）
    seen: set[tuple[str, str]] = set()
    unique: list[SensitivityFlip] = []
    for f in flips:
        key = (f.variable, f.change)
        if key not in seen:
            seen.add(key)
            unique.append(f)
    return unique
