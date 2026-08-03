from decision_engine.preference.bradley_terry import Comparison, fit_preferences


def test_clear_ordering_recovered():
    # 0 > 1 > 2，每对都问到
    comparisons = [
        Comparison(0, 1, "left", 0.8),
        Comparison(1, 2, "left", 0.8),
        Comparison(0, 2, "left", 0.9),
    ]
    fit = fit_preferences(3, comparisons)
    assert fit.weights[0] > fit.weights[1] > fit.weights[2]
    assert abs(sum(fit.weights) - 1.0) < 1e-9
    assert fit.conflict_pairs == 0
    assert fit.consistency == 1.0


def test_equal_answers_give_similar_weights():
    comparisons = [Comparison(0, 1, "equal")] * 3
    fit = fit_preferences(2, comparisons)
    assert abs(fit.weights[0] - fit.weights[1]) < 0.05


def test_incomparable_ignored():
    fit = fit_preferences(2, [Comparison(0, 1, "incomparable")])
    assert fit.comparisons_used == 0


def test_conflicting_answers_raise_uncertainty_not_confidence():
    consistent = fit_preferences(
        2, [Comparison(0, 1, "left", 0.8), Comparison(0, 1, "left", 0.8)]
    )
    conflicted = fit_preferences(
        2, [Comparison(0, 1, "left", 0.8), Comparison(0, 1, "right", 0.8)]
    )
    assert conflicted.conflict_pairs == 1
    assert conflicted.consistency < 1.0
    # 冲突回答的不确定度必须高于一致回答
    assert conflicted.uncertainty[0] > consistent.uncertainty[0]
    # 冲突时权重接近均等，不得给出伪高置信的极端权重
    assert abs(conflicted.weights[0] - 0.5) < 0.1


def test_more_comparisons_lower_uncertainty():
    few = fit_preferences(2, [Comparison(0, 1, "left")])
    many = fit_preferences(2, [Comparison(0, 1, "left")] * 6)
    assert many.uncertainty[0] < few.uncertainty[0]


def test_strength_affects_weight_gap():
    weak = fit_preferences(2, [Comparison(0, 1, "left", 0.1)] * 3)
    strong = fit_preferences(2, [Comparison(0, 1, "left", 1.0)] * 3)
    assert strong.weights[0] - strong.weights[1] > weak.weights[0] - weak.weights[1]
