from decision_engine.engine import run_analysis
from decision_engine.types import CriterionSpec, EvaluationSpec, OptionSpec


def make_case(veto=False):
    options = [OptionSpec("a", "方案A"), OptionSpec("b", "方案B")]
    criteria = [
        CriterionSpec("perf", "性能", weight=0.6, weight_uncertainty=0.1),
        CriterionSpec("price", "价格", weight=0.4, weight_uncertainty=0.1, direction="lower_better"),
    ]
    if veto:
        criteria.append(
            CriterionSpec(
                "warranty", "保修", weight=0.2, criterion_type="veto", veto_threshold=0.3
            )
        )
    evaluations = [
        EvaluationSpec("a", "perf", 0.9, 0.05),
        EvaluationSpec("a", "price", 0.6, 0.05),  # lower_better: 有效值0.4
        EvaluationSpec("b", "perf", 0.5, 0.05),
        EvaluationSpec("b", "price", 0.3, 0.05),  # 有效值0.7
    ]
    return options, criteria, evaluations


def test_dominant_option_wins_with_high_probability():
    options = [OptionSpec("a", "A"), OptionSpec("b", "B")]
    criteria = [CriterionSpec("q", "质量", weight=1.0, weight_uncertainty=0.05)]
    evaluations = [
        EvaluationSpec("a", "q", 0.9, 0.05),
        EvaluationSpec("b", "q", 0.2, 0.05),
    ]
    result = run_analysis(options, criteria, evaluations, seed=7)
    assert result.winner == "a"
    assert result.winner_probability["a"] > 0.95
    assert result.ranking_stability > 0.95


def test_fixed_seed_fully_reproducible():
    options, criteria, evaluations = make_case()
    r1 = run_analysis(options, criteria, evaluations, seed=123)
    r2 = run_analysis(options, criteria, evaluations, seed=123)
    assert r1.to_dict() == r2.to_dict()


def test_veto_eliminates_regardless_of_scores():
    """硬约束零违反：保修低于底线的方案即使全面占优也必须淘汰。"""
    options, criteria, evaluations = make_case(veto=True)
    evaluations += [
        EvaluationSpec("a", "warranty", 0.1, 0.05),  # A 违反保修底线
        EvaluationSpec("b", "warranty", 0.9, 0.05),
    ]
    result = run_analysis(options, criteria, evaluations, seed=42)
    assert [e.option_key for e in result.eliminated] == ["a"]
    assert result.winner == "b"
    assert "a" not in result.winner_probability


def test_user_marked_ineligible_excluded():
    options = [
        OptionSpec("a", "A", eligible=False, ineligible_reason="超出预算"),
        OptionSpec("b", "B"),
    ]
    criteria = [CriterionSpec("q", "质量", weight=1.0)]
    evaluations = [EvaluationSpec("a", "q", 0.99), EvaluationSpec("b", "q", 0.5)]
    result = run_analysis(options, criteria, evaluations)
    assert result.winner == "b"
    assert result.eliminated[0].reason == "超出预算"


def test_close_race_flags_instability_and_flips():
    options = [OptionSpec("a", "A"), OptionSpec("b", "B")]
    criteria = [
        CriterionSpec("x", "标准X", weight=0.5, weight_uncertainty=0.3),
        CriterionSpec("y", "标准Y", weight=0.5, weight_uncertainty=0.3),
    ]
    evaluations = [
        EvaluationSpec("a", "x", 0.8, 0.2),
        EvaluationSpec("a", "y", 0.3, 0.2),
        EvaluationSpec("b", "x", 0.3, 0.2),
        EvaluationSpec("b", "y", 0.78, 0.2),
    ]
    result = run_analysis(options, criteria, evaluations, seed=42)
    assert result.ranking_stability < 0.9
    assert len(result.sensitivity_flips) > 0
    assert len(result.critical_variables) > 0


def test_minimax_regret_prefers_robust_option():
    # A 期望略高但波动极大；B 稳健。最坏情况后悔应偏向 B。
    options = [OptionSpec("a", "激进"), OptionSpec("b", "稳健")]
    criteria = [CriterionSpec("q", "质量", weight=1.0, weight_uncertainty=0.05)]
    evaluations = [
        EvaluationSpec("a", "q", 0.62, 0.35),
        EvaluationSpec("b", "q", 0.60, 0.02),
    ]
    result = run_analysis(options, criteria, evaluations, seed=42)
    assert result.max_regret["b"] < result.max_regret["a"]
    assert result.minimax_regret_option == "b"


def test_aspiration_warning_reported_not_eliminated():
    options = [OptionSpec("a", "A"), OptionSpec("b", "B")]
    criteria = [
        CriterionSpec("q", "质量", weight=0.8),
        CriterionSpec(
            "noise", "噪音", weight=0.2, criterion_type="threshold", minimum_acceptable=0.5
        ),
    ]
    evaluations = [
        EvaluationSpec("a", "q", 0.9),
        EvaluationSpec("a", "noise", 0.3),
        EvaluationSpec("b", "q", 0.5),
        EvaluationSpec("b", "noise", 0.8),
    ]
    result = run_analysis(options, criteria, evaluations, seed=42)
    assert result.winner == "a"  # 不淘汰
    assert any("噪音" in w for w in result.aspiration_warnings)
