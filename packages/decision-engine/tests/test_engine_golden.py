"""算法黄金回归（方案阶段8）。

固定输入 + 固定 seed 的输出被钉死在这里。任何算法改动导致数值变化时，
本测试会失败——改动者必须显式更新黄金值并在提交说明中记录原因，
同时 bump ALGORITHM_VERSION（decision_runs 依赖它解释历史结论）。
"""

import pytest

from decision_engine.engine import ALGORITHM_VERSION, run_analysis
from decision_engine.types import CriterionSpec, EvaluationSpec, OptionSpec


def golden_case():
    options = [OptionSpec("a", "方案A"), OptionSpec("b", "方案B")]
    criteria = [
        CriterionSpec(
            "perf", "性能", weight=0.6, weight_uncertainty=0.1, curve_type="diminishing"
        ),
        CriterionSpec(
            "price",
            "价格",
            weight=0.4,
            weight_uncertainty=0.15,
            curve_type="loss_averse",
            direction="lower_better",
        ),
    ]
    evaluations = [
        EvaluationSpec("a", "perf", 0.85, 0.1),
        EvaluationSpec("a", "price", 0.7, 0.1),
        EvaluationSpec("b", "perf", 0.55, 0.1),
        EvaluationSpec("b", "price", 0.35, 0.1),
    ]
    return options, criteria, evaluations


def test_golden_regression_seed_42():
    options, criteria, evaluations = golden_case()
    r = run_analysis(options, criteria, evaluations, seed=42)

    assert ALGORITHM_VERSION == "engine-0.1.0"
    assert r.winner == "b"
    assert r.ranking == ["b", "a"]
    assert r.deterministic_utilities["a"] == pytest.approx(0.6021337323, abs=1e-9)
    assert r.deterministic_utilities["b"] == pytest.approx(0.7701699195, abs=1e-9)
    assert r.winner_probability == {"a": 0.085, "b": 0.915}
    assert r.ranking_stability == 0.915
    assert r.expected_utility_gap == pytest.approx(0.1332516603, abs=1e-9)
    assert r.minimax_regret_option == "b"
    assert r.max_regret["a"] == pytest.approx(0.4018995599, abs=1e-9)
    assert r.max_regret["b"] == pytest.approx(0.1794206039, abs=1e-9)
    assert r.critical_variables == [
        "score:方案A:价格",
        "score:方案B:价格",
        "score:方案B:性能",
    ]
    assert r.sensitivity_flips == []
