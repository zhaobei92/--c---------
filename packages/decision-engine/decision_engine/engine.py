"""决策引擎门面：硬约束过滤 → MAUT → 蒙特卡洛 → 后悔 → 敏感性。

完全确定性：固定 seed 时输出完全一致。禁止在本包内调用任何 LLM。
"""

from decision_engine.constraints.eligibility import filter_eligible
from decision_engine.constraints.lexicographic import lexicographic_order
from decision_engine.monte_carlo.simulate import (
    DEFAULT_ITERATIONS,
    deterministic_utilities,
    simulate,
)
from decision_engine.sensitivity import find_flips
from decision_engine.types import (
    AnalysisResult,
    CriterionSpec,
    EvaluationSpec,
    OptionSpec,
)

# 0.2.0：按分布类型逐单元采样（beta/triangular/categorical）+ lexicographic 规则
ALGORITHM_VERSION = "engine-0.2.0"


def run_analysis(
    options: list[OptionSpec],
    criteria: list[CriterionSpec],
    evaluations: list[EvaluationSpec],
    n_iterations: int = DEFAULT_ITERATIONS,
    seed: int = 42,
) -> AnalysisResult:
    eligible, eliminated, warnings = filter_eligible(options, criteria, evaluations)

    utils = deterministic_utilities(eligible, criteria, evaluations)
    lex_criteria = [
        c
        for c in sorted(criteria, key=lambda c: -max(c.weight, 0.0))
        if c.criterion_type == "lexicographic"
    ]
    if lex_criteria:
        eval_map = {(e.option_key, e.criterion_key): e for e in evaluations}

        def effective(o_key: str, c: CriterionSpec) -> float:
            ev = eval_map.get((o_key, c.key))
            raw = ev.expected_value if ev else 0.5
            return 1.0 - raw if c.direction == "lower_better" else raw

        lex_values = {
            o.key: [effective(o.key, c) for c in lex_criteria] for o in eligible
        }
        ranking = lexicographic_order([o.key for o in eligible], lex_values, utils)
    else:
        ranking = sorted(utils, key=utils.get, reverse=True)

    mc = simulate(eligible, criteria, evaluations, n_iterations=n_iterations, seed=seed)
    flips = find_flips(eligible, criteria, evaluations)

    return AnalysisResult(
        algorithm_version=ALGORITHM_VERSION,
        seed=seed,
        n_iterations=n_iterations,
        eliminated=eliminated,
        deterministic_utilities=utils,
        ranking=ranking,
        winner_probability=mc["winner_probability"],
        ranking_stability=mc["ranking_stability"],
        expected_utility_gap=mc["expected_utility_gap"],
        minimax_regret_option=mc["minimax_regret_option"],
        max_regret=mc["max_regret"],
        critical_variables=mc["critical_variables"],
        sensitivity_flips=flips,
        aspiration_warnings=warnings,
    )
