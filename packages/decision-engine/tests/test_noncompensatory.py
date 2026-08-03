"""非补偿规则（方案6.4）与分布采样（方案6.5）的补充测试。"""

from decision_engine.engine import run_analysis
from decision_engine.types import CriterionSpec, EvaluationSpec, OptionSpec


def test_lexicographic_first_priority_dominates():
    """A 在第一优先级明显更好 → 即使 B 综合效用更高也是 A 赢。"""
    options = [OptionSpec("a", "A"), OptionSpec("b", "B")]
    criteria = [
        CriterionSpec(
            "safety", "安全性", weight=0.5, criterion_type="lexicographic"
        ),
        CriterionSpec("comfort", "舒适度", weight=0.5),
    ]
    evaluations = [
        EvaluationSpec("a", "safety", 0.9, 0.03),
        EvaluationSpec("a", "comfort", 0.2, 0.03),
        EvaluationSpec("b", "safety", 0.5, 0.03),
        EvaluationSpec("b", "comfort", 0.95, 0.03),
    ]
    result = run_analysis(options, criteria, evaluations, seed=42)
    # MAUT 效用上 B 更高，但 lexicographic 第一优先级 A 明显胜出
    assert result.deterministic_utilities["b"] > result.deterministic_utilities["a"]
    assert result.ranking[0] == "a"
    assert result.winner_probability["a"] > 0.9


def test_lexicographic_tie_falls_back_to_utility():
    """第一优先级打平（容差内）→ 退回效用比较。"""
    options = [OptionSpec("a", "A"), OptionSpec("b", "B")]
    criteria = [
        CriterionSpec(
            "safety", "安全性", weight=0.5, criterion_type="lexicographic"
        ),
        CriterionSpec("comfort", "舒适度", weight=0.5),
    ]
    evaluations = [
        EvaluationSpec("a", "safety", 0.80, 0.03),
        EvaluationSpec("a", "comfort", 0.3, 0.03),
        EvaluationSpec("b", "safety", 0.78, 0.03),  # 容差内视为持平
        EvaluationSpec("b", "comfort", 0.9, 0.03),
    ]
    result = run_analysis(options, criteria, evaluations, seed=42)
    assert result.ranking[0] == "b"


def test_beta_distribution_sampling_stays_bounded_and_reproducible():
    options = [OptionSpec("a", "A"), OptionSpec("b", "B")]
    criteria = [CriterionSpec("rel", "可靠率", weight=1.0, weight_uncertainty=0.05)]
    evaluations = [
        EvaluationSpec("a", "rel", 0.9, 0.08, distribution="beta"),
        EvaluationSpec("b", "rel", 0.5, 0.2, distribution="beta"),
    ]
    r1 = run_analysis(options, criteria, evaluations, seed=7)
    r2 = run_analysis(options, criteria, evaluations, seed=7)
    assert r1.to_dict() == r2.to_dict()
    assert r1.winner == "a"
    assert 0.0 <= r1.winner_probability["b"] <= 1.0


def test_triangular_and_categorical_distributions():
    options = [OptionSpec("a", "A"), OptionSpec("b", "B")]
    criteria = [CriterionSpec("q", "质量", weight=1.0, weight_uncertainty=0.05)]
    evaluations = [
        EvaluationSpec("a", "q", 0.7, 0.1, distribution="triangular"),
        EvaluationSpec("b", "q", 0.5, 0.1, distribution="categorical"),
    ]
    result = run_analysis(options, criteria, evaluations, seed=11)
    assert result.winner == "a"
    assert abs(sum(result.winner_probability.values()) - 1.0) < 1e-9
